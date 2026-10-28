"""녹음 종료 후 자동 처리 흐름: 오디오 저장 → 전사 → 회의록 생성 → Notion 페이지 생성 → Relation 연결.

각 단계는 결과 파일/ID 가 이미 있으면 건너뛴다. 따라서 어느 단계에서 실패해도 '이어서 처리'로
성공한 단계를 다시 하지 않고(비용 중복 없이) 실패한 지점부터 계속한다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Callable

from .costs import transcription_cost
from .llm import LLMError, make_provider
from .minutes import MinutesError, generate_minutes, save_minutes
from .notion_client import NotionClient, NotionError
from .notion_sync import sync_to_notion
from .recorder import FINAL_NAME, RecorderError, audio_duration, finalize_recording, has_unfinalized_pcm
from .settings import Settings
from .storage import MeetingStore
from .transcription import OpenAITranscriber, TranscriptionError, save_transcript, transcribe_recording

log = logging.getLogger("labmeeting.pipeline")

RECOVERY_HINTS = {
    "saving_audio": "녹음 원본(PCM 조각)은 그대로 보존되어 있습니다. 디스크 공간을 확인한 뒤 '이어서 처리'를 누르세요.",
    "transcribing": "이미 전사된 조각은 저장되어 있어 다시 비용이 들지 않습니다. 원인을 해결한 뒤 '이어서 처리'를 누르세요.",
    "generating_minutes": "녹취록은 저장되어 있습니다. 원인을 해결한 뒤 '이어서 처리'를 누르면 회의록 생성부터 다시 시작합니다.",
    "creating_notion_pages": "이미 만들어진 Notion 페이지 ID 가 저장되어 있어 '이어서 처리' 시 중복 생성 없이 계속합니다.",
    "linking_pages": "두 페이지는 만들어졌습니다. Relation 설정을 확인한 뒤 '이어서 처리'를 누르세요.",
}


class SetupRequired(RuntimeError):
    pass


class Pipeline:
    def __init__(self, settings: Settings, store: MeetingStore, *,
                 transcriber_factory: Callable | None = None, provider_factory: Callable | None = None,
                 notion_factory: Callable | None = None):
        self.s = settings
        self.store = store
        self.transcriber_factory = transcriber_factory or (
            lambda s: OpenAITranscriber(s.openai_api_key, s.transcription_model, s.transcription_language))
        self.provider_factory = provider_factory or make_provider
        self.notion_factory = notion_factory or (lambda s: NotionClient(s.notion_api_key))

    def _paths(self, meeting_id: str) -> dict[str, Path]:
        return {
            "transcript_txt": self.s.transcript_dir / f"{meeting_id}.txt",
            "transcript_json": self.s.transcript_dir / f"{meeting_id}.json",
            "transcript_work": self.s.transcript_dir / meeting_id,
            "minutes_json": self.s.minutes_dir / f"{meeting_id}.json",
            "minutes_md": self.s.minutes_dir / f"{meeting_id}.md",
        }

    def run(self, meeting_id: str) -> dict:
        step = "saving_audio"

        def set_step(new_step: str, detail: str | None = None):
            nonlocal step
            step = new_step
            self.store.update(meeting_id, processing_status=new_step, step_detail=detail,
                              error=None, error_step=None, recovery_hint=None)

        try:
            meta = self.store.load(meeting_id)
            mdir = self.store.meeting_dir(meeting_id)
            p = self._paths(meeting_id)
            self.s.ensure_dirs()

            # 1. 오디오 저장 (녹음 중 꺼졌다면 여기서 복구)
            if has_unfinalized_pcm(mdir) or not ((mdir / FINAL_NAME).exists() or p["transcript_txt"].exists()):
                set_step("saving_audio")
                audio = finalize_recording(mdir)
                self.store.update(meeting_id, recording_path=str(audio), duration_seconds=round(audio_duration(audio), 1))
            audio = mdir / FINAL_NAME

            # 2. 전사
            if not p["transcript_txt"].exists():
                set_step("transcribing")
                if missing := self.s.missing_for_transcription():
                    raise SetupRequired("설정 필요: " + ", ".join(missing))
                result = transcribe_recording(
                    audio, p["transcript_work"], self.transcriber_factory(self.s),
                    chunk_seconds=self.s.transcription_chunk_seconds,
                    overlap_seconds=self.s.transcription_overlap_seconds,
                    glossary=self.s.glossary, attendees=meta.get("attendees", ""),
                    progress=lambda d, n: self.store.update(meeting_id, step_detail=f"{d}/{n}"),
                )
                save_transcript(result, meeting_id=meeting_id, model=self.s.transcription_model,
                                language=self.s.transcription_language, txt_path=p["transcript_txt"],
                                json_path=p["transcript_json"])
                meta = self.store.load(meeting_id)
                costs = dict(meta.get("costs") or {})
                costs["transcription"] = transcription_cost(self.s.transcription_model, result["billed_seconds"])
                self.store.update(meeting_id, transcript_path=str(p["transcript_txt"]),
                                  transcript_json_path=str(p["transcript_json"]), costs=costs)
            transcript = p["transcript_txt"].read_text(encoding="utf-8")

            # 3. 회의록
            if not p["minutes_json"].exists():
                set_step("generating_minutes")
                if missing := self.s.missing_for_summary():
                    raise SetupRequired("설정 필요: " + ", ".join(missing))
                meta = self.store.load(meeting_id)
                notes, cost = generate_minutes(
                    self.provider_factory(self.s), transcript,
                    meeting_date=dt.date.fromisoformat(meta["meeting_date"]), title=meta["title"],
                    attendees=meta.get("attendees", ""), glossary=self.s.glossary,
                    raw_dir=self.s.minutes_dir, meeting_id=meeting_id,
                )
                save_minutes(notes, transcript, p["minutes_json"], p["minutes_md"])
                meta = self.store.load(meeting_id)
                costs = dict(meta.get("costs") or {})
                costs["summary"] = cost
                self.store.update(meeting_id, minutes_path=str(p["minutes_json"]), minutes_md_path=str(p["minutes_md"]),
                                  costs=costs)
            notes = json.loads(p["minutes_json"].read_text(encoding="utf-8"))

            # 4~5. Notion
            meta = self.store.load(meeting_id)
            if not meta.get("relation_linked"):
                set_step("creating_notion_pages")
                if missing := self.s.missing_for_notion():
                    raise SetupRequired("Notion 설정 필요: " + ", ".join(missing))
                tinfo = json.loads(p["transcript_json"].read_text(encoding="utf-8")) if p["transcript_json"].exists() else {}
                sync_to_notion(self.notion_factory(self.s), self.s, self.store, meeting_id, notes, transcript, tinfo,
                               on_step=lambda st: set_step(st))

            return self.store.update(meeting_id, processing_status="completed", step_detail=None,
                                     error=None, error_step=None, recovery_hint=None)
        except SetupRequired as e:
            return self._fail(meeting_id, step, str(e), "화면의 '설정' 탭에서 값을 입력·저장한 뒤 '이어서 처리'를 누르세요.")
        except (RecorderError, TranscriptionError, LLMError, MinutesError, NotionError) as e:
            return self._fail(meeting_id, step, str(e), RECOVERY_HINTS.get(step))
        except Exception as e:  # 예상치 못한 오류도 상태로 남긴다
            log.exception("처리 중 예기치 않은 오류")
            return self._fail(meeting_id, step, f"예기치 않은 오류: {type(e).__name__}: {e}", RECOVERY_HINTS.get(step))

    def _fail(self, meeting_id: str, step: str, message: str, hint: str | None) -> dict:
        log.error("[%s] %s 단계 실패: %s", meeting_id, step, message)
        return self.store.update(meeting_id, processing_status="failed", error=message, error_step=step,
                                 recovery_hint=hint)


class Worker:
    """회의별 백그라운드 처리 스레드. 같은 회의를 동시에 두 번 처리하지 않는다."""

    def __init__(self):
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def is_running(self, meeting_id: str) -> bool:
        t = self._threads.get(meeting_id)
        return bool(t and t.is_alive())

    def submit(self, pipeline: Pipeline, meeting_id: str) -> bool:
        with self._lock:
            if self.is_running(meeting_id):
                return False
            t = threading.Thread(target=pipeline.run, args=(meeting_id,), name=f"pipeline-{meeting_id}", daemon=True)
            self._threads[meeting_id] = t
            t.start()
            return True


def purge_old_recordings(store: MeetingStore, days: int, today: dt.date | None = None) -> list[str]:
    """보존 기간이 지난 '처리 완료' 회의의 원본 녹음만 삭제 (사용자가 버튼을 눌렀을 때만 호출)."""
    if days <= 0:
        return []
    today = today or dt.date.today()
    removed = []
    for meta in store.list():
        if meta.get("processing_status") != "completed":
            continue
        created = dt.datetime.fromisoformat(meta["created_at"]).date()
        audio = store.meeting_dir(meta["meeting_id"]) / FINAL_NAME
        if (today - created).days > days and audio.exists():
            audio.unlink()
            store.update(meta["meeting_id"], recording_path=None, recording_deleted_at=today.isoformat())
            removed.append(meta["meeting_id"])
    return removed
