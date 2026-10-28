"""회의별 메타데이터(meeting.json) 저장소.

recordings/<meeting_id>/meeting.json 에 처리 상태와 생성된 파일·Notion 페이지 ID를 기록한다.
모든 쓰기는 임시 파일 → 원자적 교체로 수행하므로, 쓰는 도중 프로그램이 꺼져도 파일이 깨지지 않는다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

STEP_LABELS = {
    "recording": "Recording · 녹음 중",
    "interrupted": "Interrupted · 녹음 중단됨 (복구 가능)",
    "saving_audio": "Saving audio · 오디오 저장 중",
    "recorded": "Recorded · 처리 대기",
    "transcribing": "Transcribing · 전사 중",
    "generating_minutes": "Generating meeting notes · 회의록 생성 중",
    "creating_notion_pages": "Creating Notion pages · 노션 페이지 생성 중",
    "linking_pages": "Linking pages · 페이지 연결 중",
    "completed": "Completed · 완료",
    "failed": "Failed · 실패",
}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(meeting_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(meeting_id, threading.Lock())


def new_meeting_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, default=str))


class MeetingStore:
    def __init__(self, recording_dir: Path):
        self.recording_dir = recording_dir

    def meeting_dir(self, meeting_id: str) -> Path:
        return self.recording_dir / meeting_id

    def path(self, meeting_id: str) -> Path:
        return self.meeting_dir(meeting_id) / "meeting.json"

    def create(self, *, meeting_date: dt.date, title: str, attendees: str = "", related_page_url: str = "",
               device: str = "", meeting_id: str | None = None) -> dict:
        meeting_id = meeting_id or new_meeting_id()
        meta = {
            "meeting_id": meeting_id,
            "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "meeting_date": meeting_date.isoformat(),
            "title": title,
            "attendees": attendees,
            "related_page_url": related_page_url,
            "device": device,
            "recording_path": None,
            "duration_seconds": None,
            "transcript_path": None,
            "transcript_json_path": None,
            "minutes_path": None,
            "minutes_md_path": None,
            "meeting_page_id": None,
            "meeting_page_url": None,
            "ai_notes_page_id": None,
            "ai_notes_page_url": None,
            "relation_linked": False,
            "processing_status": "recording",
            "error": None,
            "error_step": None,
            "recovery_hint": None,
            "costs": {},
            "history": [],
        }
        self.meeting_dir(meeting_id).mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path(meeting_id), meta)
        return meta

    def load(self, meeting_id: str) -> dict:
        return json.loads(self.path(meeting_id).read_text(encoding="utf-8"))

    def update(self, meeting_id: str, **changes: Any) -> dict:
        with _lock(meeting_id):
            meta = self.load(meeting_id)
            if "processing_status" in changes and changes["processing_status"] != meta.get("processing_status"):
                meta["history"].append({
                    "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "status": changes["processing_status"],
                })
            meta.update(changes)
            atomic_write_json(self.path(meeting_id), meta)
            return meta

    def list(self) -> list[dict]:
        if not self.recording_dir.exists():
            return []
        metas = []
        for p in sorted(self.recording_dir.glob("*/meeting.json"), reverse=True):
            try:
                metas.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return metas
