import datetime as dt

import pytest
from conftest import MEETING_DATE, TRANSCRIPT, sample_notes
from fake_notion import AI_DS, MEETING_DS, FakeNotion

from labmeeting.minutes import normalize
from labmeeting.notion_client import NotionClient, NotionError, parse_notion_id
from labmeeting.notion_sync import ensure_page, marker_block, resolve_targets, sync_to_notion, to_notion


def _meeting(store):
    return store.create(meeting_date=MEETING_DATE, title="261028(수) 회의", attendees="김교수, 민수")


def _sync(client, settings, store, mid, transcript=TRANSCRIPT):
    notes = normalize(sample_notes(), transcript, MEETING_DATE)
    return sync_to_notion(client, settings, store, mid, notes, transcript, {"timestamps": "approximate"})


def test_parse_notion_id_from_url():
    url = "https://www.notion.so/lab/회의록-1111111111111111111111111111aaaa?v=0123456789abcdef0123456789abcdef"
    assert parse_notion_id(url) == "11111111-1111-1111-1111-11111111aaaa"  # ?v= 뷰 ID 는 무시
    assert parse_notion_id("11111111111111111111111111111111") == "11111111-1111-1111-1111-111111111111"
    with pytest.raises(NotionError):
        parse_notion_id("https://www.notion.so/no-id-here")


# 7 + 8. 페이지 ID 저장, 두 페이지 Relation 연결
def test_creates_pages_saves_ids_and_links(settings, store, notion_client, fake_notion):
    mid = _meeting(store)["meeting_id"]
    meta = _sync(notion_client, settings, store, mid)

    assert meta["meeting_page_id"] and meta["ai_notes_page_id"] and meta["relation_linked"]
    assert meta["meeting_page_url"].startswith("https://www.notion.so/")
    meeting_pages, ai_pages = fake_notion.pages_in(MEETING_DS), fake_notion.pages_in(AI_DS)
    assert len(meeting_pages) == 1 and len(ai_pages) == 1
    assert fake_notion.relation(meta["meeting_page_id"], "AI 정리본") == [meta["ai_notes_page_id"]]
    assert fake_notion.relation(meta["ai_notes_page_id"], "회의록") == [meta["meeting_page_id"]]  # 양방향
    title = meeting_pages[0]["properties"]["이름"]["title"][0]["plain_text"]
    assert title == "261028(수) 회의"
    assert ai_pages[0]["properties"]["제목"]["title"][0]["plain_text"] == "261028 회의 AI 녹취+요약본"
    assert meeting_pages[0]["properties"]["날짜"]["date"] == {"start": "2026-10-28"}
    # 본문 첫 블록은 중복 방지 표식, 녹취록 원문 포함
    ai_blocks = fake_notion.children[meta["ai_notes_page_id"]]
    assert mid in ai_blocks[0]["paragraph"]["rich_text"][0]["plain_text"]
    all_text = " ".join(t["plain_text"] for b in ai_blocks for t in b[b["type"]].get("rich_text", []))
    assert "데이터 분석은 R 말고 Python 으로 하는 걸로 하죠." in all_text


# 9. 같은 회의를 다시 처리해도 중복 생성되지 않음
def test_rerun_does_not_duplicate(settings, store, notion_client, fake_notion):
    mid = _meeting(store)["meeting_id"]
    _sync(notion_client, settings, store, mid)
    store.update(mid, relation_linked=False)
    _sync(notion_client, settings, store, mid)
    assert len(fake_notion.pages_in(MEETING_DS)) == 1 and len(fake_notion.pages_in(AI_DS)) == 1


def test_recovers_page_created_before_id_was_saved(settings, store, notion_client, fake_notion):
    """페이지 생성 직후 ID 저장 전에 꺼진 상황: 제목+표식으로 기존 페이지를 찾아 재사용."""
    mid = _meeting(store)["meeting_id"]
    t = resolve_targets(notion_client, settings)
    notion_client.create_page({"parent": {"type": "data_source_id", "data_source_id": t.meeting_ds},
                               "properties": {"이름": {"title": [{"type": "text", "text": {"content": "261028(수) 회의"}}]}},
                               "children": [to_notion(marker_block(mid))]})
    # 같은 제목이지만 다른 회의의 페이지 (재사용하면 안 됨)
    notion_client.create_page({"parent": {"type": "data_source_id", "data_source_id": t.meeting_ds},
                               "properties": {"이름": {"title": [{"type": "text", "text": {"content": "261028(수) 회의"}}]}},
                               "children": [to_notion(marker_block("other-meeting"))]})
    meta = _sync(notion_client, settings, store, mid)
    assert len(fake_notion.pages_in(MEETING_DS)) == 2  # 새로 만들지 않음
    assert mid in fake_notion.children[meta["meeting_page_id"]][0]["paragraph"]["rich_text"][0]["plain_text"]


# 10. API 오류 후 복구
def test_resume_after_relation_failure(settings, store, notion_client, fake_notion):
    mid = _meeting(store)["meeting_id"]
    fake_notion.fail("PATCH", r"^/pages/", status=400, code="validation_error")
    with pytest.raises(NotionError):
        _sync(notion_client, settings, store, mid)
    meta = store.load(mid)
    assert meta["meeting_page_id"] and meta["ai_notes_page_id"] and not meta["relation_linked"]
    meta = _sync(notion_client, settings, store, mid)
    assert meta["relation_linked"]
    assert len(fake_notion.pages_in(MEETING_DS)) == 1 and len(fake_notion.pages_in(AI_DS)) == 1


def test_resume_after_partial_block_append(settings, store, notion_client, fake_notion):
    long_transcript = "\n".join(f"[≈00:{i // 60:02d}:{i % 60:02d}] " + "가" * 1500 for i in range(250))
    mid = _meeting(store)["meeting_id"]
    fake_notion.fail("PATCH", r"^/blocks/", status=400, code="validation_error", times=1)
    with pytest.raises(NotionError):
        _sync(notion_client, settings, store, mid, long_transcript)
    done_before = store.load(mid)["ai_notes_blocks_done"]
    meta = _sync(notion_client, settings, store, mid, long_transcript)
    blocks = fake_notion.children[meta["ai_notes_page_id"]]
    assert done_before == 100 and len(blocks) == meta["ai_notes_blocks_done"] > 200
    assert sum("가" * 100 in b.get("paragraph", {}).get("rich_text", [{}])[0].get("plain_text", "")
               for b in blocks if b["type"] == "paragraph") == 250  # 녹취 줄 중복·누락 없음


def test_retries_on_server_errors(settings, store, notion_client, fake_notion):
    fake_notion.fail("POST", r"^/pages$", status=503, times=2)
    fake_notion.fail("PATCH", r"^/pages/", status=429, times=1, code="rate_limited")
    mid = _meeting(store)["meeting_id"]
    assert _sync(notion_client, settings, store, mid)["relation_linked"]
    assert len(fake_notion.pages_in(MEETING_DS)) == 1


def test_wrong_relation_property_name(settings, notion_client):
    settings.notion_meeting_relation_property = "없는속성"
    with pytest.raises(NotionError, match="AI 정리본"):  # 사용 가능한 속성을 알려줌
        resolve_targets(notion_client, settings)


def test_wrong_database_id(settings, notion_client):
    settings.notion_ai_notes_database_id = "99999999999999999999999999999999"
    with pytest.raises(NotionError, match="Connections"):
        resolve_targets(notion_client, settings)


def test_permission_denied_message(settings, notion_client, fake_notion):
    fake_notion.fail("GET", r"^/databases/", status=403, code="restricted_resource")
    with pytest.raises(NotionError, match="권한"):
        resolve_targets(notion_client, settings)


def test_single_property_relation_with_reverse_prop(settings, store):
    fake = FakeNotion(dual=False)
    client = NotionClient("ntn_x0000000", transport=fake.transport, sleep=lambda s: None, min_interval=0)
    settings.notion_ai_notes_relation_property = "회의록"
    mid = _meeting(store)["meeting_id"]
    meta = _sync(client, settings, store, mid)
    assert fake.relation(meta["ai_notes_page_id"], "회의록") == [meta["meeting_page_id"]]


def test_single_property_relation_without_reverse_prop_warns(settings, store):
    fake = FakeNotion(dual=False)
    client = NotionClient("ntn_x0000000", transport=fake.transport, sleep=lambda s: None, min_interval=0)
    mid = _meeting(store)["meeting_id"]
    meta = _sync(client, settings, store, mid)
    assert meta["relation_linked"] and meta["notion_warnings"]


def test_trashed_page_is_recreated(settings, store, notion_client, fake_notion):
    mid = _meeting(store)["meeting_id"]
    meta = _sync(notion_client, settings, store, mid)
    fake_notion.pages[meta["ai_notes_page_id"]]["in_trash"] = True
    store.update(mid, relation_linked=False)
    meta2 = _sync(notion_client, settings, store, mid)
    assert meta2["ai_notes_page_id"] != meta["ai_notes_page_id"]
