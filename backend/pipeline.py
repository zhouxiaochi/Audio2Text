from pathlib import Path

from backend.config import Settings
from backend.db import JobStore
from backend.documents import write_artifacts
from backend.media import ensure_tools, normalize, probe, split_chunks
from backend.merge import merge_segments
from backend.models import JobRecord, JobStatus, Stage, TranscriptSegment
from backend.remote import RemoteClient


class Pipeline:
    """Restart-safe transcription pipeline with per-stage and per-chunk checkpoints."""

    def __init__(self, settings: Settings, store: JobStore, remote: RemoteClient):
        self.settings = settings
        self.store = store
        self.remote = remote

    def _progress(self, job_id: str, stage: Stage, progress: float) -> None:
        self.store.update_job(job_id, stage=stage, progress=progress)

    async def run(self, job: JobRecord) -> None:
        ensure_tools()
        job_dir = self.settings.jobs_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        source = Path(job.source_path)

        metadata = self.store.get_checkpoint(job.id, "probe")
        if metadata is None:
            self._progress(job.id, Stage.PROBING, 0.03)
            metadata = await probe(source)
            self.store.put_checkpoint(job.id, "probe", metadata)
            self.store.update_job(job.id, metadata=metadata)

        normalized = job_dir / "normalized.wav"
        if not normalized.exists():
            self._progress(job.id, Stage.NORMALIZING, 0.08)
            await normalize(source, normalized)

        chunks = self.store.get_checkpoint(job.id, "chunks")
        if chunks is None or any(not Path(chunk["path"]).exists() for chunk in chunks):
            self._progress(job.id, Stage.CHUNKING, 0.12)
            chunks = await split_chunks(
                normalized,
                job_dir / "chunks",
                metadata["duration"],
                self.settings.chunk_seconds,
                self.settings.overlap_seconds,
            )
            self.store.put_checkpoint(job.id, "chunks", chunks)

        chunk_results: list[list[TranscriptSegment]] = []
        for index, chunk in enumerate(chunks):
            key = f"transcript:{index}"
            saved = self.store.get_checkpoint(job.id, key)
            if saved is None:
                self._progress(
                    job.id,
                    Stage.TRANSCRIBING,
                    0.15 + 0.55 * index / max(1, len(chunks)),
                )
                segments, usage, cost, request_id = await self.remote.transcribe(
                    Path(chunk["path"]), chunk["start"]
                )
                self.store.record_usage(
                    job.id,
                    job.user_id,
                    "/audio/transcriptions",
                    self.settings.transcription_model,
                    usage,
                    cost,
                    request_id,
                )
                saved = [segment.model_dump() for segment in segments]
                self.store.put_checkpoint(job.id, key, saved)
            chunk_results.append([TranscriptSegment.model_validate(item) for item in saved])

        merged_data = self.store.get_checkpoint(job.id, "merged")
        if merged_data is None:
            self._progress(job.id, Stage.MERGING, 0.72)
            merged = merge_segments(chunk_results)
            merged_data = [segment.model_dump() for segment in merged]
            self.store.put_checkpoint(job.id, "merged", merged_data)
        segments = [TranscriptSegment.model_validate(item) for item in merged_data]
        if not segments:
            raise RuntimeError("transcription produced no segments")

        speaker_data = self.store.get_checkpoint(job.id, "speakers")
        if speaker_data is None:
            self._progress(job.id, Stage.SPEAKERS, 0.78)
            speaker_result, speaker_costs = await self.remote.infer_speakers(segments)
            for usage, cost, request_id in speaker_costs:
                self.store.record_usage(
                    job.id,
                    job.user_id,
                    "/chat/completions",
                    self.settings.speaker_model,
                    usage,
                    cost,
                    request_id,
                )
            speaker_data = speaker_result.model_dump()
            self.store.put_checkpoint(job.id, "speakers", speaker_data)
        speakers = {item["segment_id"]: item["speaker"] for item in speaker_data["turns"]}
        for segment in segments:
            segment.speaker = speakers[segment.id]

        translation_data = self.store.get_checkpoint(job.id, "translations")
        if translation_data is None:
            self._progress(job.id, Stage.TRANSLATING, 0.86)
            translation_result, translation_costs = await self.remote.translate_zh(segments)
            for usage, cost, request_id in translation_costs:
                self.store.record_usage(
                    job.id,
                    job.user_id,
                    "/chat/completions",
                    self.settings.llm_model,
                    usage,
                    cost,
                    request_id,
                )
            translation_data = translation_result.model_dump()
            self.store.put_checkpoint(job.id, "translations", translation_data)
        translations = {
            item["segment_id"]: item["translation_zh"]
            for item in translation_data["translations"]
        }
        for segment in segments:
            segment.translation_zh = translations[segment.id]

        self._progress(job.id, Stage.RENDERING, 0.95)
        write_artifacts(job_dir, self.store.get_job(job.id), segments)
        self.store.update_job(
            job.id,
            status=JobStatus.COMPLETED,
            stage=Stage.COMPLETED,
            progress=1.0,
            error=None,
        )
        self.store.settle_job_cost(job.id, job.user_id)
