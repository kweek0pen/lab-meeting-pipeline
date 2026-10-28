import datetime as dt

import pytest

from labmeeting.titles import ai_notes_page_title, calendar_hint, meeting_page_title, weekday_ko


# 1. YYMMDD(요일) 형식
def test_meeting_title_format():
    assert meeting_page_title(dt.date(2026, 10, 28)) == "261028(수) 회의"


# 2. 요일이 실제 날짜와 일치
@pytest.mark.parametrize("date, expected", [
    (dt.date(2026, 10, 26), "월"), (dt.date(2026, 10, 27), "화"), (dt.date(2026, 10, 28), "수"),
    (dt.date(2026, 10, 29), "목"), (dt.date(2026, 10, 30), "금"), (dt.date(2026, 10, 31), "토"),
    (dt.date(2026, 11, 1), "일"), (dt.date(2024, 2, 29), "목"), (dt.date(2027, 1, 1), "금"),
])
def test_weekday_matches_calendar(date, expected):
    assert weekday_ko(date) == expected
    assert meeting_page_title(date).endswith(f"({expected}) 회의")


def test_ai_notes_title():
    d = dt.date(2026, 10, 28)
    assert ai_notes_page_title(d) == "261028 회의 AI 녹취+요약본"
    assert ai_notes_page_title(d, "261028(수) 회의") == "261028 회의 AI 녹취+요약본"
    assert ai_notes_page_title(d, "랩미팅 특별회의") == "랩미팅 특별회의 AI 녹취+요약본"


def test_calendar_hint_next_week():
    hint = calendar_hint(dt.date(2026, 10, 28))
    assert "다음 주: 11-02(월)" in hint and "11-06(금)" in hint
