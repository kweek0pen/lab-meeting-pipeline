"""회의 제목·날짜 형식."""

from __future__ import annotations

import datetime as dt

WEEKDAYS_KO = "월화수목금토일"


def weekday_ko(date: dt.date) -> str:
    return WEEKDAYS_KO[date.weekday()]


def meeting_page_title(date: dt.date) -> str:
    """회의록 모음 DB 페이지 제목: 261028(수) 회의"""
    return f"{date:%y%m%d}({weekday_ko(date)}) 회의"


def ai_notes_page_title(date: dt.date, meeting_title: str | None = None) -> str:
    """AI 녹취+요약본 DB 페이지 제목: 261028 회의 AI 녹취+요약본"""
    if meeting_title and meeting_title != meeting_page_title(date):
        return f"{meeting_title} AI 녹취+요약본"
    return f"{date:%y%m%d} 회의 AI 녹취+요약본"


def calendar_hint(date: dt.date, weeks: int = 6) -> str:
    """상대적 기한('다음 주 금요일')을 LLM 이 정확히 계산하도록 주는 달력."""
    start = date - dt.timedelta(days=date.weekday())  # 회의 주 월요일
    lines = []
    for w in range(weeks):
        week_start = start + dt.timedelta(weeks=w)
        label = "이번 주" if w == 0 else ("다음 주" if w == 1 else f"{w}주 뒤")
        days = " ".join(
            f"{d:%m-%d}({weekday_ko(d)})" for d in (week_start + dt.timedelta(days=i) for i in range(7))
        )
        lines.append(f"- {label}: {days}")
    return "\n".join(lines)


def fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
