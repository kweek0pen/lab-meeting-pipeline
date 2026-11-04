"""회의록 생성 · 검증 · 렌더링.

1) LLM 호출 → JSON 스키마 검증. 실패 시 제한적으로 재시도, 끝내 실패하면 원본 응답을 파일로 보존.
2) 코드 수준 안전장치(normalize):
   - 녹취록에서 지명 근거를 찾을 수 없는 담당자는 제거(미정)
   - 날짜 형식이 아니거나, 원래 기한 표현이 녹취록에 없거나, 회의일 이전인 기한은 제거(미정)
   - 근거가 없거나 '해석 불확실'인 확정 업무/결정은 잠정·미결로 낮춤
   - 각 인용문이 녹취록에 실제로 있는지 표시(found_in_transcript)
   - 담당자를 지운 경우 개조식 본문의 "(이름)" 표기도 함께 지움
3) 화면/Notion/Markdown 에 공통으로 쓰는 문서 구조(blocks)로 변환.
   본문은 사람이 쓴 회의록처럼 짧게: TO-DO 요약 → 안건별 개조식 → 확인 필요 → (접힌) 근거.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

from pydantic import ValidationError

from .costs import add_llm_costs, llm_cost
from .llm import LLMError, LLMProvider
from .schema import BASIS_KO, DECISION_STATE_KO, MeetingNotes
from .settings import PROMPTS_DIR
from .storage import atomic_write_text
from .titles import calendar_hint, weekday_ko

log = logging.getLogger("labmeeting.minutes")

MAX_ATTEMPTS = 3
HONORIFICS = ("교수님", "선생님", "박사님", "선배님", "선배", "님", "씨", "군", "양")


class MinutesError(RuntimeError):
    def __init__(self, message: str, raw_paths: list[Path] | None = None):
        super().__init__(message)
        self.raw_paths = raw_paths or []


# ---------------------------------------------------------------- 프롬프트
def system_prompt() -> str:
    return (PROMPTS_DIR / "minutes_system.md").read_text(encoding="utf-8")


def user_prompt(transcript: str, *, meeting_date: dt.date, title: str, attendees: str, glossary: str) -> str:
    return f"""## 회의 정보
- 회의 제목: {title}
- 회의 날짜: {meeting_date.isoformat()} ({weekday_ko(meeting_date)}요일) — 상대적 기한은 이 날짜 기준으로 해석
- 참석자 명단(이름 표기 참고용, 담당자 추정 근거 아님): {attendees or "(입력 없음)"}
- 연구실 용어/고유명사: {glossary or "(입력 없음)"}

## 달력 (상대적 기한 계산용)
{calendar_hint(meeting_date)}

## 녹취록
각 줄 앞의 [시간]은 녹음 시작 기준이며, ≈ 는 추정 시각이다. 전사 오류가 있을 수 있다.
<transcript>
{transcript}
</transcript>

위 지침에 따라 회의록 JSON 을 작성하라. meeting_title 은 "{title}", meeting_date 는 "{meeting_date.isoformat()}" 로 한다."""


# ---------------------------------------------------------------- 생성
def generate_minutes(provider: LLMProvider, transcript: str, *, meeting_date: dt.date, title: str,
                     attendees: str, glossary: str, raw_dir: Path, meeting_id: str) -> tuple[dict, dict]:
    """(정규화된 회의록 dict, 비용 dict) 반환."""
    system = system_prompt()
    user = user_prompt(transcript, meeting_date=meeting_date, title=title, attendees=attendees, glossary=glossary)
    cost: dict | None = None
    raw_paths: list[Path] = []
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = provider.generate_json(system, user, MeetingNotes)
        cost = add_llm_costs(cost, llm_cost(result.model, result.input_tokens, result.output_tokens))
        try:
            notes = MeetingNotes.model_validate_json(result.text)
        except ValidationError as e:
            last_error = str(e).splitlines()[0]
            raw_path = raw_dir / f"{meeting_id}.raw_attempt{attempt}.txt"
            atomic_write_text(raw_path, result.text)
            raw_paths.append(raw_path)
            log.warning("회의록 JSON 검증 실패 (%d/%d): %s", attempt, MAX_ATTEMPTS, last_error)
            continue
        return normalize(notes.model_dump(), transcript, meeting_date), cost
    raise MinutesError(
        f"AI 응답을 {MAX_ATTEMPTS}번 받았지만 올바른 회의록 형식이 아니었습니다 ({last_error}). "
        "녹취록과 원본 응답은 저장되어 있습니다. '이어서 처리'로 다시 시도할 수 있습니다.",
        raw_paths,
    )


# ---------------------------------------------------------------- 안전장치
def _squash(text: str) -> str:
    return re.sub(r"[\s\.,!?…·\"'“”‘’\-~]", "", text or "").lower()


def _strip_timestamps(transcript: str) -> str:
    return re.sub(r"^\[[^\]]*\]\s*(화자 [A-Z]+:\s*)?", "", transcript, flags=re.M)


def _core_name(name: str) -> str:
    name = name.strip()
    for h in HONORIFICS:
        if name.endswith(h) and len(name) > len(h):
            return name[: -len(h)].strip()
    return name


def _quote_in_transcript(quote: str, squashed_transcript: str) -> bool:
    q = _squash(quote)
    if not q:
        return False
    if q in squashed_transcript:
        return True
    # 모델이 앞뒤를 조금 잘라 인용한 경우를 허용: 가운데 24자가 있으면 일치로 본다
    if len(q) > 30:
        mid = len(q) // 2
        return q[mid - 12: mid + 12] in squashed_transcript
    return False


def _empty_to_none(obj):
    if isinstance(obj, dict):
        return {k: _empty_to_none(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_empty_to_none(v) for v in obj]
    if isinstance(obj, str) and not obj.strip():
        return None
    return obj


def _mark_evidence(evidence: list[dict], squashed: str) -> list[dict]:
    for ev in evidence:
        ev["found_in_transcript"] = _quote_in_transcript(ev.get("quote", ""), squashed)
    return evidence


def _note(item: dict, message: str) -> None:
    item.setdefault("validation_notes", []).append(message)


def normalize(notes: dict, transcript: str, meeting_date: dt.date) -> dict:
    body = _strip_timestamps(transcript)
    squashed = _squash(body)

    # 모든 evidence 에 원문 일치 여부 표시
    for section in ("decisions", "action_items"):
        for item in notes.get(section, []):
            _mark_evidence(item.get("evidence", []), squashed)
    removed_names: set[str] = set()

    for i, item in enumerate(notes.get("action_items", []), start=1):
        item["task_id"] = f"A{i}"
        item.setdefault("validation_notes", [])

        # 담당자: 지명 발언에 이름이 있고, 그 이름이 녹취록에도 있어야 함
        kept = []
        assignee_ev = item.get("assignee_evidence") or ""
        for name in item.get("assignees", []):
            core = _core_name(name)
            if (core and _squash(core) in _squash(assignee_ev) and _squash(core) in squashed
                    and _quote_in_transcript(assignee_ev, squashed)):
                kept.append(core)
            else:
                _note(item, f"담당자 '{name}': 녹취록에서 명시적 지명 근거를 찾지 못해 미정으로 처리")
                removed_names.update({name.strip(), core})
        item["assignees"] = kept
        item["assignee"] = ", ".join(kept) if kept else None
        if not kept:
            item["assignee_evidence"] = ""

        # 기한: YYYY-MM-DD 이고, 원래 표현이 녹취록에 있으며, 회의일 이후여야 함
        due, due_text = (item.get("due_date") or "").strip(), (item.get("due_date_text") or "").strip()
        if due:
            reason = None
            try:
                due_date = dt.date.fromisoformat(due)
            except ValueError:
                reason = f"기한 '{due}' 이(가) 날짜 형식이 아님"
            else:
                if not due_text or _squash(due_text) not in squashed:
                    reason = f"기한 {due}: 녹취록에서 기한 발언을 찾지 못함"
                elif due_date < meeting_date:
                    reason = f"기한 {due}: 회의 날짜보다 이전"
            if reason:
                _note(item, reason + " → 미정으로 처리")
                item["due_date"] = ""

        # 확정 업무인데 근거가 없거나 해석 불확실 → 잠정으로 낮춤
        has_ev = any(ev.get("quote") for ev in item.get("evidence", []))
        if item.get("commitment") == "confirmed" and (not has_ev or item.get("basis") == "uncertain"):
            item["commitment"] = "tentative"
            _note(item, "근거 발언이 없거나 해석이 불확실하여 '잠정'으로 분류")
        conf = item.get("confidence")
        if isinstance(conf, (int, float)):
            item["confidence"] = min(1.0, max(0.0, float(conf)))

    for i, d in enumerate(notes.get("decisions", []), start=1):
        d["decision_id"] = f"D{i}"
        has_ev = any(ev.get("quote") for ev in d.get("evidence", []))
        if d.get("state") == "confirmed" and (not has_ev or d.get("basis") == "uncertain"):
            d["state"] = "unresolved"
            _note(d, "근거 발언이 없거나 해석이 불확실하여 '미결'로 분류")

    # 근거 없이 지운 담당자가 개조식 본문에 "(이름)"으로 남아 있으면 함께 지움
    def scrub(items):
        for it in items:
            for name in removed_names:
                if name:
                    it["text"] = re.sub(rf"\s*\(\s*{re.escape(name)}\s*\)", "", it["text"])
            scrub(it.get("children") or [])

    for sec in notes.get("outline", []):
        scrub(sec.get("items") or [])

    notes["generated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return _empty_to_none(notes)


# ---------------------------------------------------------------- 렌더링 (공통 블록)
# 블록: {"type": "h2|h3|p|bullet|todo|quote|toggle|divider|callout", "text": str, "checked": bool, "children": [...]}
def _b(type_: str, text: str = "", **kw) -> dict:
    return {"type": type_, "text": text, **kw}


def _short_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def _todo_line(a: dict) -> str:
    text = a["task"]
    if a.get("due_date"):
        text += f" ~{_short_date(a['due_date'])}"
    elif a.get("due_date_text"):
        text += f" ({a['due_date_text']})"
    if a.get("commitment") != "confirmed":
        text += " (잠정)"
    if a.get("needs_confirmation"):
        text += " (확인 필요)"
    return text


def todo_callout(notes: dict) -> dict:
    """맨 위 TO-DOs: 확정된 할 일만, 명시적으로 지명된 사람별 → 담당 미정 순.
    잠정(아이디어 수준) 할 일은 본문 맥락 아래 체크박스로만 남겨 맨 위를 짧게 유지한다."""
    groups: dict[str, list[dict]] = {}
    for a in notes.get("action_items") or []:
        if a.get("status") == "done" or a.get("commitment") != "confirmed":
            continue
        for person in a.get("assignees") or ["담당 미정"]:
            groups.setdefault(person, []).append(a)
    order = [p for p in groups if p != "담당 미정"] + (["담당 미정"] if "담당 미정" in groups else [])
    children = [_b("bullet", person, children=[_b("todo", _todo_line(a)) for a in groups[person]]) for person in order]
    return _b("callout", "TO-DOs", emoji="✅", children=children or [_b("p", "정해진 할 일 없음")])


def _outline_item(item: dict) -> dict:
    return _b("todo" if item.get("todo") else "bullet", item["text"],
              children=[_outline_item(c) for c in item.get("children") or []])


def outline_blocks(notes: dict) -> list[dict]:
    blocks = []
    for sec in notes.get("outline") or []:
        blocks.append(_b("h3", sec["title"]))
        blocks += [_outline_item(i) for i in sec.get("items") or []]
    return blocks


def _ev_blocks(evidence: list[dict] | None) -> list[dict]:
    out = []
    for ev in evidence or []:
        ts = f"[{ev['timestamp']}] " if ev.get("timestamp") else ""
        tag = " (의역)" if ev.get("is_paraphrase") else ""
        warn = "" if ev.get("found_in_transcript") or ev.get("is_paraphrase") else " ⚠ 녹취록 원문과 일치 확인 안 됨"
        out.append(_b("quote", f"{ts}{ev.get('quote') or ''}{tag}{warn}"))
    return out


def _check_notes(item: dict) -> list[dict]:
    notes = ([item["note"]] if item.get("note") else []) + [f"자동 검증: {n}" for n in item.get("validation_notes") or []]
    return [_b("p", " / ".join(notes), color="gray")] if notes else []


def evidence_toggle(notes: dict) -> dict:
    children = []
    for d in notes.get("decisions") or []:
        state = DECISION_STATE_KO.get(d["state"], d["state"])
        children.append(_b("bullet", f"[결정·{state}] {d['statement']} ({BASIS_KO.get(d.get('basis'), '')})",
                           children=_ev_blocks(d.get("evidence")) + _check_notes(d)))
    for a in notes.get("action_items") or []:
        who = a.get("assignee") or "미정"
        children.append(_b("bullet", f"[할 일] {_todo_line(a)} — 담당 {who} ({BASIS_KO.get(a.get('basis'), '')})",
                           children=_ev_blocks(a.get("evidence")) + _check_notes(a)))
    return _b("toggle", "근거 보기 — 결정·할 일의 원문 발언과 자동 검증 결과",
              children=children or [_b("p", "없음")])


def minutes_blocks(notes: dict) -> list[dict]:
    """간결한 회의록 본문: TO-DOs → 안건별 개조식 → 확인 필요 → (접힌) 근거."""
    blocks = [todo_callout(notes)] + outline_blocks(notes)
    questions = notes.get("needs_clarification") or []
    if questions:
        blocks.append(_b("h3", "확인 필요"))
        blocks += [_b("bullet", q) for q in questions]
    blocks.append(evidence_toggle(notes))
    return blocks


def transcript_blocks(transcript: str, max_chars: int = 1800) -> list[dict]:
    """녹취록 줄을 Notion 문단 글자 제한(2000자) 안쪽으로 묶는다."""
    blocks, buf = [], ""
    for line in transcript.splitlines():
        while len(line) > max_chars:  # 아주 긴 한 줄
            if buf:
                blocks.append(_b("p", buf))
                buf = ""
            blocks.append(_b("p", line[:max_chars]))
            line = line[max_chars:]
        if len(buf) + len(line) + 1 > max_chars:
            blocks.append(_b("p", buf))
            buf = ""
        buf = f"{buf}\n{line}" if buf else line
    if buf:
        blocks.append(_b("p", buf))
    return blocks


def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> str:
    out = []
    pad = "  " * depth
    for b in blocks:
        t, text = b["type"], b.get("text", "")
        if t == "h1":
            out.append(f"# {text}")
        elif t == "h2":
            out.append(f"\n## {text}")
        elif t == "h3":
            out.append(f"\n### {text}")
        elif t == "bullet":
            out.append(f"{pad}- {text}")
        elif t == "todo":
            out.append(f"{pad}- [{'x' if b.get('checked') else ' '}] {text}")
        elif t == "quote":
            out.append(f"{pad}> {text}")
        elif t in ("toggle", "callout"):
            out.append(f"\n**{text}**")
            out.append(blocks_to_markdown(b.get("children") or [], depth))
            continue
        elif t == "divider":
            out.append("---")
        else:
            out.append(f"{pad}{text}" if depth else f"\n{text}")
        if b.get("children"):
            out.append(blocks_to_markdown(b["children"], depth + 1))
    return "\n".join(out)


def save_minutes(notes: dict, transcript: str, json_path: Path, md_path: Path) -> None:
    atomic_write_text(json_path, json.dumps(notes, ensure_ascii=False, indent=2))
    md = [f"# {notes.get('meeting_title')}", f"회의 날짜: {notes.get('meeting_date')}",
          blocks_to_markdown(minutes_blocks(notes)), "\n## 전체 녹취록 (원문)", transcript]
    atomic_write_text(md_path, "\n".join(md))
