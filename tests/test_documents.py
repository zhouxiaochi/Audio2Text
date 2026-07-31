import zipfile
from pathlib import Path

from backend.documents import generate_markdown, render_docx
from backend.models import JobRecord, JobStatus, TranscriptSegment


def test_markdown_and_docx_rendering(tmp_path: Path):
    job = JobRecord(
        id="job-1",
        user_id="user-1",
        original_filename="meeting.wav",
        source_path="meeting.wav",
        status=JobStatus.PROCESSING,
        stage="rendering",
        progress=0.9,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:01:00+00:00",
    )
    segments = [
        TranscriptSegment(
            id=0,
            start=1,
            end=4,
            text="Good morning.",
            speaker="Speaker 1",
            translation_zh="早上好。",
        )
    ]

    markdown = generate_markdown(job, segments)
    destination = tmp_path / "result.docx"
    render_docx(markdown, destination)

    assert "Speaker 1" in markdown
    assert "早上好。" in markdown
    assert zipfile.is_zipfile(destination)
