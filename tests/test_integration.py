"""실제 Notion API 통합 테스트. RUN_INTEGRATION=1 이고 .env 가 설정된 경우에만 실행된다.

    RUN_INTEGRATION=1 uv run pytest tests/test_integration.py

주의: 실제 두 데이터베이스를 읽기만 한다(페이지를 만들지 않음).
"""

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="RUN_INTEGRATION=1 일 때만 실행")
def test_real_notion_targets_resolve():
    from labmeeting.notion_client import NotionClient
    from labmeeting.notion_sync import resolve_targets
    from labmeeting.settings import load_settings

    s = load_settings()
    if s.missing_for_notion():
        pytest.skip("Notion 설정 없음: " + ", ".join(s.missing_for_notion()))
    t = resolve_targets(NotionClient(s.notion_api_key), s)
    assert t.meeting_ds and t.ai_ds
