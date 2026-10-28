"""Notion 등록: 회의록 모음 DB 페이지 + AI 녹취+요약본 DB 페이지 생성, Relation 연결, 저장 확인.

중복 방지 / 복구:
- 페이지를 만들자마자 page_id 를 meeting.json 에 저장한다. 다시 실행하면 저장된 페이지를 그대로 이어서 쓴다.
- 만약 '페이지 생성 직후, ID 저장 전'에 프로그램이 꺼졌다면: 같은 제목의 페이지를 검색해 본문 첫 줄의
  meeting_id 표식으로 같은 회의 페이지인지 확인하고 재사용한다.
- 본문 블록은 100개씩 나눠 붙이며, 몇 개까지 붙였는지 저장해 실패 지점부터 이어서 붙인다.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable

from .minutes import _action_blocks, _b, minutes_blocks, transcript_blocks
from .notion_client import NotionClient, NotionError, parse_notion_id, same_id
from .titles import ai_notes_page_title

log = logging.getLogger("labmeeting.notion_sync")

MARKER_PREFIX = "meeting_id:"
RICH_TEXT_LIMIT = 1900
BATCH = 100


@dataclass
class Targets:
    meeting_ds: str
    meeting_title_prop: str
    ai_ds: str
    ai_title_prop: str
    meeting_relation_prop: str
    meeting_relation_prop_id: str
    relation_dual: bool
    synced_prop_on_ai: str | None  # 양방향 Relation 이면 AI DB 쪽에 자동 생기는 속성 이름
    ai_relation_prop: str | None
    ai_relation_prop_id: str | None
    meeting_date_prop: str | None
    ai_date_prop: str | None
    project_relation_prop: str | None


# ---------------------------------------------------------------- 대상 확인
def _data_source(client: NotionClient, db_value: str, label: str) -> dict:
    db_id = parse_notion_id(db_value)
    try:
        db = client.get_database(db_id)
        sources = db.get("data_sources") or []
        if not sources:
            raise NotionError(f"{label}에 데이터 소스가 없습니다.")
        return client.get_data_source(sources[0]["id"])
    except NotionError as e:
        if e.status != 404:
            raise
        try:  # 데이터베이스가 아니라 data source ID 를 넣은 경우
            return client.get_data_source(db_id)
        except NotionError:
            raise NotionError(f"[{label}] {e}", status=404) from e


def _title_prop(ds: dict) -> str:
    for name, prop in ds["properties"].items():
        if prop["type"] == "title":
            return name
    raise NotionError("데이터베이스에 제목 속성이 없습니다.")


def _check_prop(ds: dict, name: str, expected_type: str, label: str, env: str) -> dict:
    props = ds["properties"]
    if name not in props:
        candidates = [n for n, p in props.items() if p["type"] == expected_type]
        raise NotionError(f"{label}에 '{name}' 속성이 없습니다. ({env} 확인) "
                          f"사용 가능한 {expected_type} 속성: {', '.join(candidates) or '없음'}")
    if props[name]["type"] != expected_type:
        raise NotionError(f"{label}의 '{name}' 속성은 {expected_type} 형식이 아닙니다 ({props[name]['type']}). {env} 확인")
    return props[name]


def resolve_targets(client: NotionClient, settings) -> Targets:
    """두 DB 접근 권한과 속성 이름을 모두 검사한다. 문제가 있으면 무엇을 고쳐야 하는지 알려준다."""
    meeting = _data_source(client, settings.notion_meeting_database_id, "회의록 모음 DB")
    ai = _data_source(client, settings.notion_ai_notes_database_id, "AI 녹취+요약본 DB")

    rel = _check_prop(meeting, settings.notion_meeting_relation_property, "relation", "회의록 모음 DB",
                      "NOTION_MEETING_RELATION_PROPERTY")
    if not same_id(rel["relation"].get("data_source_id"), ai["id"]):
        raise NotionError(f"회의록 모음 DB 의 '{settings.notion_meeting_relation_property}' Relation 이 "
                          "AI 녹취+요약본 DB 가 아닌 다른 데이터베이스를 가리킵니다. 속성 이름 또는 DB ID 를 확인하세요.")
    dual = rel["relation"].get("type") == "dual_property"
    synced = (rel["relation"].get("dual_property") or {}).get("synced_property_name") if dual else None

    ai_rel_name = ai_rel_id = None
    if settings.notion_ai_notes_relation_property:
        ai_rel = _check_prop(ai, settings.notion_ai_notes_relation_property, "relation", "AI 녹취+요약본 DB",
                             "NOTION_AI_NOTES_RELATION_PROPERTY")
        if not same_id(ai_rel["relation"].get("data_source_id"), meeting["id"]):
            raise NotionError("AI 녹취+요약본 DB 의 Relation 속성이 회의록 모음 DB 를 가리키지 않습니다.")
        ai_rel_name, ai_rel_id = settings.notion_ai_notes_relation_property, ai_rel["id"]

    if settings.notion_meeting_date_property:
        _check_prop(meeting, settings.notion_meeting_date_property, "date", "회의록 모음 DB", "NOTION_MEETING_DATE_PROPERTY")
    if settings.notion_ai_notes_date_property:
        _check_prop(ai, settings.notion_ai_notes_date_property, "date", "AI 녹취+요약본 DB", "NOTION_AI_NOTES_DATE_PROPERTY")
    if settings.notion_meeting_project_relation_property:
        _check_prop(meeting, settings.notion_meeting_project_relation_property, "relation", "회의록 모음 DB",
                    "NOTION_MEETING_PROJECT_RELATION_PROPERTY")

    return Targets(
        meeting_ds=meeting["id"], meeting_title_prop=_title_prop(meeting), ai_ds=ai["id"], ai_title_prop=_title_prop(ai),
        meeting_relation_prop=settings.notion_meeting_relation_property, meeting_relation_prop_id=rel["id"],
        relation_dual=dual, synced_prop_on_ai=synced, ai_relation_prop=ai_rel_name, ai_relation_prop_id=ai_rel_id,
        meeting_date_prop=settings.notion_meeting_date_property or None,
        ai_date_prop=settings.notion_ai_notes_date_property or None,
        project_relation_prop=settings.notion_meeting_project_relation_property or None,
    )


# ---------------------------------------------------------------- 블록 변환
def rich_text(text: str, *, url: str | None = None, color: str | None = None) -> list[dict]:
    text = text or ""
    parts = [text[i:i + RICH_TEXT_LIMIT] for i in range(0, len(text), RICH_TEXT_LIMIT)] or [""]
    out = []
    for p in parts:
        item = {"type": "text", "text": {"content": p}}
        if url:
            item["text"]["link"] = {"url": url}
        if color:
            item["annotations"] = {"color": color}
        out.append(item)
    return out[:100]


_TYPE_MAP = {"h1": "heading_1", "h2": "heading_2", "h3": "heading_3", "p": "paragraph",
             "bullet": "bulleted_list_item", "todo": "to_do", "quote": "quote", "callout": "callout"}


def to_notion(block: dict, depth: int = 0) -> dict:
    if block["type"] == "divider":
        return {"object": "block", "type": "divider", "divider": {}}
    ntype = _TYPE_MAP[block["type"]]
    payload: dict = {"rich_text": rich_text(block.get("text", ""), url=block.get("url"), color=block.get("color"))}
    if ntype == "to_do":
        payload["checked"] = bool(block.get("checked"))
    if ntype == "callout":
        payload["icon"] = {"type": "emoji", "emoji": block.get("emoji", "ℹ️")}
    children = block.get("children") or []
    if children and ntype.startswith("heading"):
        children = []  # 제목 블록은 하위 블록 불가 (toggle 제목 제외)
    if children:
        if depth >= 1:  # 한 요청에서 2단계까지만 중첩 가능 → 더 깊은 것은 평탄화
            children = [c | {"children": []} for c in children]
        payload["children"] = [to_notion(c, depth + 1) for c in children[:BATCH]]
    return {"object": "block", "type": ntype, ntype: payload}


def marker_block(meeting_id: str) -> dict:
    return _b("p", f"{MARKER_PREFIX} {meeting_id} · lab-meeting-pipeline 자동 생성", color="gray")


def _page_title(page: dict, title_prop: str) -> str:
    return "".join(t.get("plain_text") or t.get("text", {}).get("content", "")
                   for t in page["properties"][title_prop]["title"])


def _alive(page: dict) -> bool:
    return not (page.get("archived") or page.get("in_trash"))


# ---------------------------------------------------------------- 페이지 생성 (멱등)
def find_existing_page(client: NotionClient, ds_id: str, title_prop: str, title: str, meeting_id: str) -> dict | None:
    res = client.query_data_source(ds_id, {"filter": {"property": title_prop, "title": {"equals": title}},
                                           "page_size": 20})
    for page in res.get("results", []):
        if not _alive(page):
            continue
        children = client.list_children(page["id"], page_size=3).get("results", [])
        for block in children:
            texts = block.get(block["type"], {}).get("rich_text", [])
            content = "".join(t.get("plain_text") or t.get("text", {}).get("content", "") for t in texts)
            if f"{MARKER_PREFIX} {meeting_id}" in content:
                return page
    return None


def ensure_page(client: NotionClient, *, kind: str, meta: dict, save: Callable[..., dict], ds_id: str,
                title_prop: str, title: str, properties: dict, blocks: list[dict]) -> dict:
    """kind = 'meeting' | 'ai_notes'. 이미 있으면 재사용하고, 본문은 이어서 붙인다."""
    id_key, url_key, done_key = f"{kind}_page_id", f"{kind}_page_url", f"{kind}_blocks_done"
    meeting_id = meta["meeting_id"]
    all_blocks = [marker_block(meeting_id)] + blocks
    page = None

    if meta.get(id_key):
        try:
            page = client.get_page(meta[id_key])
            if not _alive(page):
                log.info("%s 페이지가 휴지통에 있어 새로 만듭니다", kind)
                page = None
                meta = save(**{id_key: None, url_key: None, done_key: 0})
        except NotionError as e:
            if e.status != 404:
                raise
            page = None
            meta = save(**{id_key: None, url_key: None, done_key: 0})

    if page is None:
        page = find_existing_page(client, ds_id, title_prop, title, meeting_id)
        if page is not None:
            # ID 저장 전에 꺼졌던 경우. 본문이 어디까지 붙었는지 모르므로 블록 수로 추정
            existing = client.list_children(page["id"]).get("results", [])
            meta = save(**{id_key: page["id"], url_key: page.get("url"), done_key: len(existing)})
            log.info("기존 %s 페이지 재사용(중복 생성 방지): %s", kind, page["id"])

    if page is None:
        first = all_blocks[:BATCH]
        page = client.create_page({
            "parent": {"type": "data_source_id", "data_source_id": ds_id},
            "properties": properties,
            "children": [to_notion(b) for b in first],
        })
        meta = save(**{id_key: page["id"], url_key: page.get("url"), done_key: len(first)})
        log.info("%s 페이지 생성: %s", kind, page["id"])

    done = meta.get(done_key) or 0
    while done < len(all_blocks):
        batch = all_blocks[done:done + BATCH]
        client.append_children(page["id"], [to_notion(b) for b in batch])
        done += len(batch)
        meta = save(**{done_key: done})
    return page


# ---------------------------------------------------------------- Relation
def relation_ids(client: NotionClient, page: dict, prop_name: str) -> list[str]:
    prop = page["properties"].get(prop_name)
    if not prop:
        return []
    ids = [r["id"] for r in prop.get("relation", [])]
    if prop.get("has_more"):  # 25개 넘으면 속성 전용 엔드포인트로 전부 조회
        ids, cursor = [], None
        while True:
            res = client.get_page_property(page["id"], prop["id"], cursor)
            ids += [r["relation"]["id"] for r in res.get("results", []) if r.get("type") == "relation"]
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
    return ids


def link_pages(client: NotionClient, t: Targets, meeting_page_id: str, ai_page_id: str) -> list[str]:
    """회의 페이지 Relation 에 AI 페이지를 추가(기존 연결 유지)하고 양쪽이 실제로 연결됐는지 확인. 경고 목록 반환."""
    warnings = []
    page = client.get_page(meeting_page_id)
    current = relation_ids(client, page, t.meeting_relation_prop)
    if not any(same_id(i, ai_page_id) for i in current):
        client.update_page(meeting_page_id, {"properties": {
            t.meeting_relation_prop: {"relation": [{"id": i} for i in current] + [{"id": ai_page_id}]}}})

    page = client.get_page(meeting_page_id)
    if not any(same_id(i, ai_page_id) for i in relation_ids(client, page, t.meeting_relation_prop)):
        raise NotionError("Relation 업데이트 후 확인했지만 연결이 저장되지 않았습니다. 다시 시도하세요.")

    # 반대쪽 확인
    ai_page = client.get_page(ai_page_id)
    reverse_prop = t.synced_prop_on_ai if t.relation_dual else t.ai_relation_prop
    reverse_ok = bool(reverse_prop) and any(
        same_id(i, meeting_page_id) for i in relation_ids(client, ai_page, reverse_prop))
    if not reverse_ok:
        if t.ai_relation_prop:
            cur = relation_ids(client, ai_page, t.ai_relation_prop)
            client.update_page(ai_page_id, {"properties": {
                t.ai_relation_prop: {"relation": [{"id": i} for i in cur] + [{"id": meeting_page_id}]}}})
            ai_page = client.get_page(ai_page_id)
            if not any(same_id(i, meeting_page_id) for i in relation_ids(client, ai_page, t.ai_relation_prop)):
                raise NotionError("AI 녹취+요약본 쪽 Relation 저장을 확인하지 못했습니다.")
        else:
            warnings.append("Relation 이 단방향이라 AI 녹취+요약본 쪽에는 연결이 표시되지 않습니다. 양쪽 모두 연결하려면 "
                            "Notion 에서 Relation 속성을 양방향(‘Show on 상대 DB’)으로 바꾸거나 "
                            "NOTION_AI_NOTES_RELATION_PROPERTY 를 설정하세요.")
    return warnings


# ---------------------------------------------------------------- 전체 흐름
def _date_prop(value: str) -> dict:
    return {"date": {"start": value}}


def meeting_page_blocks(meta: dict, notes: dict) -> list[dict]:
    blocks = [_b("h2", "회의 정보"),
              _b("bullet", f"회의 날짜: {meta['meeting_date']}"),
              _b("bullet", f"참석자: {meta.get('attendees') or '-'}")]
    url = (meta.get("related_page_url") or "").strip()
    if url:
        blocks.append(_b("bullet", f"관련 페이지: {url}", url=url if url.startswith("http") else None))
    blocks.append(_b("callout", "상세 회의록과 전체 녹취록은 Relation 으로 연결된 'AI 녹취+요약본' 페이지에 있습니다. "
                                "AI 가 작성한 내용이므로 사실과 다를 수 있습니다.", emoji="🤖"))
    blocks.append(_b("h2", "핵심 요약"))
    blocks += [_b("bullet", s) for s in notes.get("executive_summary") or []]
    blocks.append(_b("h2", "확정된 결정"))
    confirmed = [d for d in notes.get("decisions") or [] if d["state"] == "confirmed"]
    blocks += [_b("bullet", d["statement"]) for d in confirmed] or [_b("p", "없음")]
    blocks.append(_b("h2", "Action Items"))
    actions = [a for a in notes.get("action_items") or [] if a["commitment"] == "confirmed"]
    blocks += [_action_blocks(a) | {"children": []} for a in actions] or [_b("p", "확정된 Action Item 없음")]
    return blocks


def ai_page_blocks(meta: dict, notes: dict, transcript: str, transcript_info: dict) -> list[dict]:
    blocks = [_b("callout", "AI 가 녹음을 전사·요약한 결과입니다. 원문 녹취록은 맨 아래에 그대로 있습니다. "
                            "자유롭게 수정하세요.", emoji="🤖"),
              _b("p", f"회의 날짜: {meta['meeting_date']} · 참석자: {meta.get('attendees') or '-'}")]
    blocks += minutes_blocks(notes)
    blocks.append(_b("divider"))
    blocks.append(_b("h2", "전체 녹취록 (원문)"))
    info = []
    if transcript_info.get("timestamps") == "approximate":
        info.append("≈ 표시 시각은 추정치입니다.")
    if transcript_info.get("diarization"):
        info.append("화자 라벨(A, B …)은 자동 구분 결과이며 실제 인물을 의미하지 않습니다.")
    info.append("전사 오류가 있을 수 있습니다. 원본 녹음 파일은 녹음한 컴퓨터에 보관되어 있습니다.")
    blocks.append(_b("p", " ".join(info), color="gray"))
    blocks += transcript_blocks(transcript)
    return blocks


def sync_to_notion(client: NotionClient, settings, store, meeting_id: str, notes: dict, transcript: str,
                   transcript_info: dict, on_step: Callable[[str], None] = lambda s: None) -> dict:
    meta = store.load(meeting_id)

    def save(**changes):
        return store.update(meeting_id, **changes)

    t = resolve_targets(client, settings)
    meeting_date = dt.date.fromisoformat(meta["meeting_date"])
    meeting_title = meta["title"]
    ai_title = ai_notes_page_title(meeting_date, meeting_title)

    on_step("creating_notion_pages")
    props = {t.meeting_title_prop: {"title": rich_text(meeting_title)}}
    if t.meeting_date_prop:
        props[t.meeting_date_prop] = _date_prop(meta["meeting_date"])
    if t.project_relation_prop and meta.get("related_page_url"):
        try:
            props[t.project_relation_prop] = {"relation": [{"id": parse_notion_id(meta["related_page_url"])}]}
        except NotionError:
            pass
    meeting_page = ensure_page(client, kind="meeting", meta=meta, save=save, ds_id=t.meeting_ds,
                               title_prop=t.meeting_title_prop, title=meeting_title, properties=props,
                               blocks=meeting_page_blocks(meta, notes))
    meta = store.load(meeting_id)

    ai_props = {t.ai_title_prop: {"title": rich_text(ai_title)}}
    if t.ai_date_prop:
        ai_props[t.ai_date_prop] = _date_prop(meta["meeting_date"])
    ai_page = ensure_page(client, kind="ai_notes", meta=meta, save=save, ds_id=t.ai_ds,
                          title_prop=t.ai_title_prop, title=ai_title, properties=ai_props,
                          blocks=ai_page_blocks(meta, notes, transcript, transcript_info))

    on_step("linking_pages")
    warnings = link_pages(client, t, meeting_page["id"], ai_page["id"])

    # 실제 저장 확인: 제목 · 본문
    for page_id, title_prop, title in ((meeting_page["id"], t.meeting_title_prop, meeting_title),
                                       (ai_page["id"], t.ai_title_prop, ai_title)):
        page = client.get_page(page_id)
        if _page_title(page, title_prop) != title:
            warnings.append(f"페이지 제목이 예상과 다릅니다: '{_page_title(page, title_prop)}'")
        if not client.list_children(page_id, page_size=2).get("results"):
            raise NotionError("페이지 본문이 저장되지 않았습니다. '이어서 처리'를 누르세요.")

    return save(relation_linked=True, notion_warnings=warnings)
