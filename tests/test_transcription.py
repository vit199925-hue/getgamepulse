from app.transcription import transcript_chunks


def test_chunks_preserve_full_short_transcript_and_timestamps():
    segments = [{"text": "first line", "start": 1}, {"text": "second line", "start": 4}]
    assert transcript_chunks(segments) == [
        {"id": "transcript-0", "text": "first line second line", "start": 1}
    ]


def test_long_transcript_has_bounded_evenly_distributed_sample():
    segments = [{"text": str(i) + "a" * 2300, "start": i * 10} for i in range(100)]
    result = transcript_chunks(segments)
    assert len(result) == 40
    assert result[0]["start"] == 0 and result[-1]["start"] == 990
