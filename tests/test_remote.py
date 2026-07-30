from pathlib import Path

from backend.config import Settings
from backend.models import TranscriptSegment
from backend.remote import RemoteClient


async def test_long_transcript_is_split_into_complete_batches(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        llm_batch_characters=4000,
    )
    client = RemoteClient(settings)
    segments = [
        TranscriptSegment(id=index, start=index, end=index + 1, text="word " * 160)
        for index in range(12)
    ]

    batches = client._batches(segments)

    assert len(batches) > 1
    assert [item.id for batch in batches for item in batch] == list(range(12))
    await client.close()
