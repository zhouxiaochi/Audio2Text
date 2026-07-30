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

    async def transcribe(self, path: Path, offset: float = 0) -> list[TranscriptSegment]:
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
        ]

    @staticmethod
    def _extract_json(content: str) -> dict:
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)

    async def _structured_chat(self, prompt: str, schema: type[T]) -> T:
        response = await self._request(
            "POST",
            "/chat/completions",
            json={
                "model": self.settings.llm_model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "Return only strict JSON matching the requested schema. Never omit IDs.",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
        )
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return schema.model_validate(self._extract_json(content))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise RemoteError(f"LLM returned invalid structured output: {exc}") from exc

    async def infer_speakers(self, segments: list[TranscriptSegment]) -> SpeakerInference:
        turns: list[SpeakerTurn] = []
        for batch in self._batches(segments):
            result = await self._infer_speaker_batch(batch)
            turns.extend(result.turns)
        return SpeakerInference(turns=turns)

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
    ) -> SpeakerInference:
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
        result = await self._structured_chat(prompt, SpeakerInference)
        expected = {segment.id for segment in segments}
        actual = {turn.segment_id for turn in result.turns}
        if actual != expected or len(result.turns) != len(expected):
            raise RemoteError("speaker inference did not return exactly one item per segment")
        return result

    async def translate_zh(self, segments: list[TranscriptSegment]) -> TranslationResult:
        translations: list[TranslationItem] = []
        for batch in self._batches(segments):
            result = await self._translate_batch(batch)
            translations.extend(result.translations)
        return TranslationResult(translations=translations)

    async def _translate_batch(
        self, segments: list[TranscriptSegment]
    ) -> TranslationResult:
        rows = [
            {
                "segment_id": segment.id,
                "speaker": segment.speaker,
                "text": segment.text,
            }
            for segment in segments
        ]
        prompt = (
            "Translate every transcript segment into natural Simplified Chinese, preserving meaning, "
            "names, numbers, and technical terms. Return "
            '{"translations":[{"segment_id":0,"translation_zh":"..."}]} with exactly one item per '
            f"segment. Transcript: {json.dumps(rows, ensure_ascii=False)}"
        )
        result = await self._structured_chat(prompt, TranslationResult)
        expected = {segment.id for segment in segments}
        actual = {item.segment_id for item in result.translations}
        if actual != expected or len(result.translations) != len(expected):
            raise RemoteError("translation did not return exactly one item per segment")
        return result
