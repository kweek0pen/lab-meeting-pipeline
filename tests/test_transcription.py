from conftest import FakeTranscriber, write_tone

from labmeeting.transcription import (
    Chunk, dedupe_overlap, merge_results, plan_chunks, render_transcript, transcribe_recording)


def test_plan_chunks_overlap_and_coverage():
    spans = plan_chunks(400, 180, 8)
    assert spans[0] == (0, 180)
    assert spans[1][0] == 172  # 8초 겹침
    assert spans[-1][1] == 400
    assert plan_chunks(100, 180, 8) == [(0, 100)]


def test_dedupe_overlap_removes_repeated_words():
    prev = "그래서 이번 실험에서는 샘플 수를 늘려서 다시 돌려 보자는 거죠"
    nxt = "늘려서 다시 돌려 보자는 거죠 그리고 분석은 다음 주에"
    assert dedupe_overlap(prev, nxt) == "그리고 분석은 다음 주에"
    assert dedupe_overlap(prev, "완전히 새로운 내용입니다") == "완전히 새로운 내용입니다"


def test_merge_segments_by_ownership():
    chunks = [Chunk(0, 0, 180, None), Chunk(1, 172, 352, None)]
    results = [
        {"text": "a b", "segments": [{"start": 0, "end": 5, "text": "앞"}, {"start": 173, "end": 178, "text": "겹침"}]},
        {"text": "c", "segments": [{"start": 1, "end": 6, "text": "겹침"}, {"start": 20, "end": 25, "text": "뒤"}]},
    ]
    segs = merge_results(chunks, results, 8)
    assert [s["text"] for s in segs] == ["앞", "겹침", "뒤"]
    assert segs[2]["start"] == 192


def test_render_transcript_marks_approximate():
    txt = render_transcript([{"start": 65, "text": "안녕", "approx": True, "speaker": None},
                             {"start": 3700, "text": "네", "approx": False, "speaker": "A"}])
    assert "[≈00:01:05] 안녕" in txt and "[01:01:40] 화자 A: 네" in txt


def test_transcribe_resumes_without_reprocessing(tmp_path):
    audio = write_tone(tmp_path / "rec.flac", 50)
    failing = FakeTranscriber(fail_at=2)
    try:
        transcribe_recording(audio, tmp_path / "work", failing, chunk_seconds=20, overlap_seconds=2)
    except Exception as e:
        assert "네트워크" in str(e)
    ok = FakeTranscriber()
    result = transcribe_recording(audio, tmp_path / "work", ok, chunk_seconds=20, overlap_seconds=2)
    assert ok.calls == 2  # 첫 조각은 캐시 사용, 나머지 2개만 새로 전사
    assert result["chunk_count"] == 3
    assert "조각 0" in result["text"] and "조각 2" in result["text"]
    assert not list((tmp_path / "work" / "chunks").glob("*.wav"))  # 임시 WAV 정리


def test_chunks_respect_upload_limit(tmp_path):
    from labmeeting.transcription import MAX_UPLOAD_BYTES, write_chunks

    audio = write_tone(tmp_path / "rec.flac", 30, sr=48000)
    for c in write_chunks(audio, tmp_path / "c", 10_000, 8):
        assert c.path.stat().st_size < MAX_UPLOAD_BYTES
