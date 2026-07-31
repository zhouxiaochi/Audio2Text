import asyncio
import json
import random
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from backend.config import Settings
from backend.models import (
    SpeakerInference,
    SpeakerTurn,
    TranscriptSegment,
    TranslationItem,
    TranslationResult,
    WordTimestamp,
)

T = TypeVar("T", bound=BaseModel)


class RemoteError(RuntimeError):
    pass


class RemoteClient:
    """OpenAI-compatible OpenRouter transcription and chat client."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.remote_base_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.remote_api_key}"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if not self.settings.remote_api_key:
            raise RemoteError("REMOTE_API_KEY is not configured")
        last_error: Exception | None = None
        for attempt in range(self.settings.remote_max_retries):
            try:
                response = await self.client.request(method, url, **kwargs)
                if response.status_code not in {408, 409, 425, 429} and response.status_code < 500:
                    response.raise_for_status()
                    return response
                last_error = RemoteError(
                    f"remote service returned {response.status_code}: {response.text[:500]}"
                )
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
            if attempt + 1 < self.settings.remote_max_retries:
                delay = min(20.0, 1.5 * (2**attempt)) + random.uniform(0, 0.5)
                await asyncio.sleep(delay)
        raise RemoteError(f"remote request failed after retries: {last_error}")

    @staticmethod
    def cost_metadata(response: httpx.Response) -> tuple[dict | None, float | None, str | None]:
        """Extract provider-reported usage and cost without applying price estimates."""
        payload = response.json()
        usage = payload.get("usage")
        raw_cost = usage.get("cost") if isinstance(usage, dict) else None
        if raw_cost is None and isinstance(usage, dict):
            raw_cost = usage.get("total_cost")
        if raw_cost is None:
            raw_cost = payload.get("cost")
        if raw_cost is None:
            raw_cost = response.headers.get("x-openrouter-cost")
        try:
            cost = float(raw_cost) if raw_cost is not None else None
        except (TypeError, ValueError):
            cost = None
        return usage if isinstance(usage, dict) else None, cost, response.headers.get("x-request-id")

    async def transcribe(
        self, path: Path, offset: float = 0
    ) -> tuple[list[TranscriptSegment], dict | None, float | None, str | None]:
        with path.open("rb") as audio:
            response = await self._request(
                "POST",
                "/audio/transcriptions",
                data={
                    "model": self.settings.transcription_model,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": ["segment", "word"],
                },
                files={"file": (path.name, audio, "audio/flac")},
            )
        payload = response.json()
        words_by_segment: list[list[WordTimestamp]] = []
        for segment in payload.get("segments", []):
            segment_words = [
                WordTimestamp(
                    word=item["word"],
                    start=float(item["start"]) + offset,
                    end=float(item["end"]) + offset,
                )
                for item in segment.get("words", [])
                if item.get("word") and item.get("start") is not None and item.get("end") is not None
            ]
            words_by_segment.append(segment_words)
        return [
            TranscriptSegment(
                start=float(segment["start"]) + offset,
                end=float(segment["end"]) + offset,
                text=segment["text"].strip(),
                words=words_by_segment[index],
            )
            for index, segment in enumerate(payload.get("segments", []))
            if segment.get("text", "").strip()
        ], *self.cost_metadata(response)

    @staticmethod
    def _extract_json(content: str) -> dict:
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)

    async def _structured_chat(
        self, prompt: str, schema: type[T], model: str
    ) -> tuple[T, dict | None, float | None, str | None]:
        last_error: Exception | None = None
        messages = [
            {
                "role": "system",
                "content": "Return only strict JSON matching the requested schema. Never omit IDs.",
            },
            {"role": "user", "content": prompt},
        ]
        for attempt in range(3):
            response = await self._request(
                "POST",
                "/chat/completions",
                json={
                    "model": model,
                    "response_format": {"type": "json_object"},
                    "messages": messages,
                },
            )
            try:
                content = response.json()["choices"][0]["message"]["content"]
                return schema.model_validate(self._extract_json(content)), *self.cost_metadata(response)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                messages.extend(
                    [
                        {"role": "assistant", "content": content if "content" in locals() else "{}"},
                        {
                            "role": "user",
                            "content": (
                                f"That response failed schema validation: {exc}. "
                                "Return a corrected complete JSON object only."
                            ),
                        },
                    ]
                )
        raise RemoteError(f"LLM returned invalid structured output after retries: {last_error}")

    async def _complete_id_set(
        self,
        prompt: str,
        schema: type[T],
        expected: set[int],
        items_attribute: str,
        id_attribute: str,
        model: str,
    ) -> tuple[T, dict | None, float | None, str | None]:
        last_ids: set[int] = set()
        for attempt in range(3):
            suffix = ""
            if attempt:
                missing = sorted(expected - last_ids)
                suffix = (
                    f"\nYour previous response was incomplete. Return exactly these segment IDs: "
                    f"{sorted(expected)}. "
                    f"Missing IDs were: {missing}. Do not duplicate IDs."
                )
            result, usage, cost, request_id = await self._structured_chat(prompt + suffix, schema, model)
            items = getattr(result, items_attribute)
            ids = [getattr(item, id_attribute) for item in items]
            last_ids = set(ids)
            if last_ids == expected and len(ids) == len(expected):
                return result, usage, cost, request_id
        raise RemoteError(
            f"LLM did not return exactly one item per segment; expected {sorted(expected)}, "
            f"received {sorted(last_ids)}"
        )

    async def infer_speakers(
        self, segments: list[TranscriptSegment]
    ) -> tuple[SpeakerInference, list[tuple[dict | None, float | None, str | None]]]:
        turns: list[SpeakerTurn] = []
        costs = []
        for batch in self._batches(segments):
            result, usage, cost, request_id = await self._infer_speaker_batch(batch)
            turns.extend(result.turns)
            costs.append((usage, cost, request_id))
        return SpeakerInference(turns=turns), costs

    def _batches(self, segments: list[TranscriptSegment]) -> list[list[TranscriptSegment]]:
        """Split long transcripts without splitting an individual timed segment."""
        batches: list[list[TranscriptSegment]] = []
        current: list[TranscriptSegment] = []
        characters = 0
        limit = max(4000, self.settings.llm_batch_characters)
        for segment in segments:
            size = len(segment.text) + 120
            if current and characters + size > limit:
                batches.append(current)
                current = []
                characters = 0
            current.append(segment)
            characters += size
        if current:
            batches.append(current)
        return batches

    async def _infer_speaker_batch(
        self, segments: list[TranscriptSegment]
    ) -> tuple[SpeakerInference, dict | None, float | None, str | None]:
        rows = [
            {"segment_id": segment.id, "start": segment.start, "end": segment.end, "text": segment.text}
            for segment in segments
        ]
        prompt = (
            "Infer stable speaker labels from this transcript. Use labels like Speaker 1 or a real "
            "name only when the text gives strong evidence. Return "
            '{"turns":[{"segment_id":0,"speaker":"Speaker 1"}]} with exactly one turn per segment. '
            f"Transcript: {json.dumps(rows, ensure_ascii=False)}"
        )
        expected = {segment.id for segment in segments if segment.id is not None}
        return await self._complete_id_set(
            prompt,
            SpeakerInference,
            expected,
            "turns",
            "segment_id",
            self.settings.speaker_model,
        )

    async def translate_zh(
        self, segments: list[TranscriptSegment]
    ) -> tuple[TranslationResult, list[tuple[dict | None, float | None, str | None]]]:
        translations: list[TranslationItem] = []
        costs = []
        for batch in self._batches(segments):
            result, usage, cost, request_id = await self._translate_batch(batch)
            translations.extend(result.translations)
            costs.append((usage, cost, request_id))
        return TranslationResult(translations=translations), costs

    async def _translate_batch(
        self, segments: list[TranscriptSegment]
    ) -> tuple[TranslationResult, dict | None, float | None, str | None]:
        rows = [
            {
                "segment_id": segment.id,
                "speaker": segment.speaker,
                "text": segment.text,
            }
            for segment in segments
        ]
        prompt = (
            "Translate each transcript segment independently into natural Simplified Chinese, "
            "preserving meaning, names, numbers, and technical terms. The translation for an ID "
            "must contain only that ID's source text; never move text between IDs. Return "
            '{"translations":[{"segment_id":0,"translation_zh":"..."}]} with exactly one item per '
            f"segment. Transcript: {json.dumps(rows, ensure_ascii=False)}"
        )
        expected = {segment.id for segment in segments if segment.id is not None}
        return await self._complete_id_set(
            prompt,
            TranslationResult,
            expected,
            "translations",
            "segment_id",
            self.settings.llm_model,
        )
