"""녹음 → 전사 → 회의록 → Notion 전체 흐름 (모든 외부 API 는 가짜)."""

from conftest import MEETING_DATE, FakeProvider, FakeTranscriber, notes_json, write_tone
from fake_notion import AI_DS, MEETING_DS

from labmeeting.notion_client import NotionClient
from labmeeting.pipeline import Pipeline, purge_old_recordings


def _transcript_text(i):
    return {0: "오늘은 실험 계획 얘기부터 하죠. 민수 씨가 다음 주 금요일까지 실험 계획을 수정해 주세요.",
            1: "데이터 분석은 R 말고 Python 으로 하는 걸로 하죠."}.get(i, "")


def _setup(settings, store, fake_notion, provider=None, transcriber=None):
    meta = store.create(meeting_date=MEETING_DATE, title="261028(수) 회의")
    write_tone(store.meeting_dir(meta["meeting_id"]) / "recording.flac", 30)
    transcriber = transcriber or FakeTranscriber(_transcript_text)
    provider = provider or FakeProvider([notes_json()])
    client = NotionClient("ntn_x0000000", transport=fake_notion.transport, sleep=lambda s: None, min_interval=0)
    p = Pipeline(settings, store, transcriber_factory=lambda s: transcriber,
                 provider_factory=lambda s: provider, notion_factory=lambda s: client)
    settings.transcription_chunk_seconds = 20
    settings.transcription_overlap_seconds = 2
    return meta["meeting_id"], p, transcriber, provider


def test_full_pipeline(settings, store, fake_notion):
    mid, p, tr, pr = _setup(settings, store, fake_notion)
    meta = p.run(mid)
    assert meta["processing_status"] == "completed", meta.get("error")
    for key in ("transcript_path", "minutes_path", "minutes_md_path", "meeting_page_id", "ai_notes_page_id"):
        assert meta[key]
    assert meta["relation_linked"]
    assert meta["costs"]["transcription"]["usd"] is not None and meta["costs"]["summary"]["usd"] is not None
    assert [h["status"] for h in meta["history"]][-5:] == [
        "transcribing", "generating_minutes", "creating_notion_pages", "linking_pages", "completed"]
    # 다시 실행해도 아무 API 도 다시 부르지 않음
    calls = (tr.calls, pr.calls, len(fake_notion.pages))
    assert p.run(mid)["processing_status"] == "completed"
    assert (tr.calls, pr.calls, len(fake_notion.pages)) == calls


def test_failure_then_resume_skips_finished_steps(settings, store, fake_notion):
    provider = FakeProvider(["{bad", "{bad", "{bad", notes_json()])
    mid, p, tr, pr = _setup(settings, store, fake_notion, provider=provider)
    meta = p.run(mid)
    assert meta["processing_status"] == "failed" and meta["error_step"] == "generating_minutes"
    assert meta["recovery_hint"]
    transcribe_calls = tr.calls
    meta = p.run(mid)
    assert meta["processing_status"] == "completed"
    assert tr.calls == transcribe_calls  # 전사는 다시 하지 않음


def test_missing_keys_does_not_crash(settings, store, fake_notion):
    settings.notion_api_key = ""
    mid, p, *_ = _setup(settings, store, fake_notion)
    meta = p.run(mid)
    assert meta["processing_status"] == "failed" and "NOTION_API_KEY" in meta["error"]
    assert meta["minutes_path"]  # Notion 이전 단계는 완료·보존
    assert not fake_notion.pages


def test_notion_failure_resume_no_duplicates(settings, store, fake_notion):
    mid, p, *_ = _setup(settings, store, fake_notion)
    fake_notion.fail("PATCH", r"^/pages/", status=400, code="validation_error")
    assert p.run(mid)["error_step"] == "linking_pages"
    assert p.run(mid)["processing_status"] == "completed"
    assert len(fake_notion.pages_in(MEETING_DS)) == 1 and len(fake_notion.pages_in(AI_DS)) == 1


def test_purge_only_completed_and_old(settings, store, fake_notion):
    import datetime as dt

    mid, p, *_ = _setup(settings, store, fake_notion)
    p.run(mid)
    assert purge_old_recordings(store, 0) == []  # 0 = 무기한 보존
    assert purge_old_recordings(store, 30) == []  # 아직 30일 안 됨
    future = dt.date.today() + dt.timedelta(days=31)
    assert purge_old_recordings(store, 30, today=future) == [mid]
    assert not (store.meeting_dir(mid) / "recording.flac").exists()
