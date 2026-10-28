import json

import pytest
from conftest import MEETING_DATE, TRANSCRIPT, FakeProvider, notes_json, sample_notes

from labmeeting.minutes import MinutesError, blocks_to_markdown, generate_minutes, minutes_blocks, normalize


def _action(**kw):
    a = sample_notes()["action_items"][0]
    a.update(kw)
    return a


def _norm(actions):
    return normalize(sample_notes(action_items=actions), TRANSCRIPT, MEETING_DATE)["action_items"]


# 4. 명시적으로 지명된 담당자는 유지 (경칭 제거)
def test_explicit_assignee_kept():
    a = _norm([_action()])[0]
    assert a["assignee"] == "민수"
    assert a["due_date"] == "2026-11-06"
    assert a["commitment"] == "confirmed"


# 3. 담당자가 명시되지 않으면 null 유지
def test_unassigned_stays_null():
    a = _norm([_action(assignees=[], assignee_evidence="",
                       evidence=[{"quote": "실험 계획을 다음 주까지 수정해야 할 것 같습니다.", "is_paraphrase": False,
                                  "timestamp": "≈00:00:20"}])])[0]
    assert a["assignee"] is None and a["assignees"] == []


def test_invented_assignee_removed():
    # 모델이 지명 근거 없이 '지현'을 담당자로 넣은 경우 → 코드가 제거
    a = _norm([_action(assignees=["지현"], assignee_evidence="실험 계획을 다음 주까지 수정해야 할 것 같습니다.")])[0]
    assert a["assignee"] is None
    assert any("지현" in n for n in a["validation_notes"])


def test_assignee_without_quote_in_transcript_removed():
    a = _norm([_action(assignee_evidence="민수 씨가 해 주세요")])[0]  # 녹취록에 없는 지명 발언
    assert a["assignee"] is None


# 5. 기한이 없으면 임의 날짜를 만들지 않음
def test_no_deadline_stays_null():
    a = _norm([_action(due_date="", due_date_text="")])[0]
    assert a["due_date"] is None


def test_invented_deadline_removed():
    a = _norm([_action(due_date="2026-11-10", due_date_text="11월 10일까지")])[0]  # 녹취록에 없는 표현
    assert a["due_date"] is None
    assert any("기한" in n for n in a["validation_notes"])


def test_malformed_or_past_deadline_removed():
    assert _norm([_action(due_date="다음주 금요일")])[0]["due_date"] is None
    assert _norm([_action(due_date="2026-10-01")])[0]["due_date"] is None


def test_confirmed_without_evidence_downgraded():
    a = _norm([_action(evidence=[])])[0]
    assert a["commitment"] == "tentative"
    d = normalize(sample_notes(decisions=[{**sample_notes()["decisions"][0], "basis": "uncertain"}]),
                  TRANSCRIPT, MEETING_DATE)["decisions"][0]
    assert d["state"] == "unresolved"


def test_evidence_traceability_flag():
    notes = normalize(sample_notes(), TRANSCRIPT, MEETING_DATE)
    assert notes["decisions"][0]["evidence"][0]["found_in_transcript"] is True
    a = _norm([_action(evidence=[{"quote": "전혀 없는 말", "is_paraphrase": False, "timestamp": ""}])])[0]
    assert a["evidence"][0]["found_in_transcript"] is False


# 6. 잘못된 JSON 응답 처리: 재시도 후 성공
def test_invalid_json_retried_then_succeeds(tmp_path):
    provider = FakeProvider(["{not json", notes_json()])
    notes, cost = generate_minutes(provider, TRANSCRIPT, meeting_date=MEETING_DATE, title="261028(수) 회의",
                                   attendees="", glossary="", raw_dir=tmp_path, meeting_id="m1")
    assert provider.calls == 2
    assert notes["action_items"][0]["assignee"] == "민수"
    assert cost["input_tokens"] == 2000  # 두 번 호출한 비용 합산
    assert (tmp_path / "m1.raw_attempt1.txt").read_text() == "{not json"


def test_invalid_json_gives_up_and_preserves_raw(tmp_path):
    bad = json.dumps({"meeting_title": "x"})  # 필수 필드 누락
    provider = FakeProvider([bad, "", "{"])
    with pytest.raises(MinutesError) as exc:
        generate_minutes(provider, TRANSCRIPT, meeting_date=MEETING_DATE, title="t", attendees="", glossary="",
                         raw_dir=tmp_path, meeting_id="m2")
    assert len(exc.value.raw_paths) == 3
    assert all(p.exists() for p in exc.value.raw_paths)


def test_render_sections_present():
    notes = normalize(sample_notes(), TRANSCRIPT, MEETING_DATE)
    md = blocks_to_markdown(minutes_blocks(notes))
    for heading in ["핵심 요약", "주요 주제와 맥락", "추론 과정 재구성", "확정된 결정", "제안 및 검토한 대안",
                    "방향 전환", "확정된 Action Items", "잠정 업무 및 확인 필요", "미결 사항", "관련 녹취 발췌"]:
        assert heading in md
    assert "담당: 민수" in md and "기한: 2026-11-06" in md
