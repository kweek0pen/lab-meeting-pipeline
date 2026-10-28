"""최소한의 Notion REST API 클라이언트 (API 버전 2025-09-03, data source 방식).

- 429·5xx·네트워크 오류는 지수형 백오프로 재시도(Retry-After 준수)
- 오류는 사용자가 이해할 수 있는 한국어 안내와 함께 NotionError 로 변환
- 토큰은 헤더에만 들어가며 로그/오류 메시지에 포함하지 않는다
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

import httpx

log = logging.getLogger("labmeeting.notion")

NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"
MAX_RETRIES = 5


class NotionError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def parse_notion_id(value: str) -> str:
    """Notion URL 또는 ID 에서 32자리 ID 를 뽑아 하이픈 형식으로 돌려준다."""
    value = (value or "").strip()
    path = value.split("?")[0].split("#")[0]
    matches = re.findall(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", path)
    if not matches:
        raise NotionError(f"Notion ID 를 인식할 수 없습니다: '{value[:80]}'. 데이터베이스 링크 전체를 붙여 넣으세요.")
    raw = matches[-1].replace("-", "").lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def same_id(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.replace("-", "").lower() == b.replace("-", "").lower()


def _friendly(status: int, code: str, message: str, path: str) -> str:
    if status == 401:
        return "Notion 토큰이 올바르지 않습니다. 설정의 NOTION_API_KEY 를 확인하세요."
    if status == 403:
        return ("Notion Integration 에 이 작업 권한이 없습니다. Integration 설정의 Capabilities 에서 "
                "'Read/Update/Insert content' 가 켜져 있는지, 워크스페이스 관리자가 승인했는지 확인하세요.")
    if status == 404:
        return ("Notion 에서 대상을 찾을 수 없습니다. ① 데이터베이스 ID/링크가 맞는지 ② 해당 데이터베이스 페이지 "
                "오른쪽 위 ••• → Connections(연결)에 이 Integration 을 추가했는지 확인하세요. "
                f"(요청: {path})")
    if status == 400:
        return f"Notion 요청 형식 오류: {message}"
    return f"Notion 오류 ({status} {code}): {message}"


class NotionClient:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, min_interval: float = 0.34):
        if not token:
            raise NotionError("NOTION_API_KEY 가 설정되지 않았습니다.")
        self._http = httpx.Client(
            base_url=BASE_URL, transport=transport, timeout=60,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
                     "Content-Type": "application/json"},
        )
        self._sleep = sleep
        self._min_interval = min_interval  # Notion 평균 요청 한도(초당 약 3회) 준수
        self._last = 0.0

    def request(self, method: str, path: str, *, json: Any = None, params: dict | None = None) -> dict:
        for attempt in range(MAX_RETRIES + 1):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self._http.request(method, path, json=json, params=params)
            except httpx.TransportError as e:
                if attempt == MAX_RETRIES:
                    raise NotionError("네트워크 오류로 Notion 에 연결하지 못했습니다. 인터넷 연결을 확인하세요.") from e
                self._sleep(min(2 ** attempt, 30))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_RETRIES:
                    break
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** attempt, 30)
                log.warning("Notion %s %s → %s, %.0f초 후 재시도", method, path, resp.status_code, delay)
                self._sleep(delay)
                continue
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                code, message = body.get("code", ""), body.get("message", resp.text[:200])
                raise NotionError(_friendly(resp.status_code, code, message, path), status=resp.status_code, code=code)
            return resp.json()
        raise NotionError("Notion 서버가 계속 응답하지 않습니다(요청 한도 또는 서버 오류). 잠시 후 '이어서 처리'를 누르세요.",
                          status=resp.status_code)

    # ---- 편의 메서드 ----
    def get_database(self, database_id: str) -> dict:
        return self.request("GET", f"/databases/{database_id}")

    def get_data_source(self, data_source_id: str) -> dict:
        return self.request("GET", f"/data_sources/{data_source_id}")

    def query_data_source(self, data_source_id: str, body: dict) -> dict:
        return self.request("POST", f"/data_sources/{data_source_id}/query", json=body)

    def create_page(self, body: dict) -> dict:
        return self.request("POST", "/pages", json=body)

    def get_page(self, page_id: str) -> dict:
        return self.request("GET", f"/pages/{page_id}")

    def update_page(self, page_id: str, body: dict) -> dict:
        return self.request("PATCH", f"/pages/{page_id}", json=body)

    def get_page_property(self, page_id: str, property_id: str, start_cursor: str | None = None) -> dict:
        params = {"start_cursor": start_cursor} if start_cursor else None
        return self.request("GET", f"/pages/{page_id}/properties/{property_id}", params=params)

    def append_children(self, block_id: str, children: list[dict]) -> dict:
        return self.request("PATCH", f"/blocks/{block_id}/children", json={"children": children})

    def list_children(self, block_id: str, page_size: int = 100) -> dict:
        return self.request("GET", f"/blocks/{block_id}/children", params={"page_size": page_size})
