from backend.merge import merge_segments, split_long_segments
from backend.models import TranscriptSegment


def test_merge_removes_overlap_and_assigns_ids():
    chunks = [
        [
            TranscriptSegment(start=0, end=5, text="Hello this is a test"),
            TranscriptSegment(start=5, end=10, text="First topic"),
        ],
        [
            TranscriptSegment(start=5, end=10, text="First topic"),
            TranscriptSegment(start=10, end=15, text="Next topic"),
        ],
    ]

    result = merge_segments(chunks)

    assert [segment.text for segment in result] == [
        "Hello this is a test",
        "First topic",
        "Next topic",
    ]
    assert [segment.id for segment in result] == [0, 1, 2]


def test_merge_trims_partial_text_overlap():
    chunks = [
        [TranscriptSegment(start=0, end=10, text="one two three")],
        [TranscriptSegment(start=8, end=15, text="two three four five")],
    ]

    result = merge_segments(chunks)

    assert [segment.text for segment in result] == ["one two three", "four five"]


def test_split_long_segments_preserves_text_and_time_range():
    source = TranscriptSegment(
        start=10,
        end=40,
        text=(
            "Hello and welcome to the programme. I'm Feifei and this is Phil. "
            "Today we are learning a useful expression. It is one size fits all."
        ),
    )

    result = split_long_segments([source], max_characters=55)

    assert len(result) > 1
    assert result[0].start == 10
    assert result[-1].end == 40
    assert [item.id for item in result] == list(range(len(result)))
    assert " ".join(item.text for item in result) == source.text
