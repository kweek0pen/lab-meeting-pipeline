from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_notion import AI_DB, MEETING_DB, FakeNotion  # noqa: E402

from labmeeting.llm import LLMResult  # noqa: E402
from labmeeting.notion_client import NotionClient  # noqa: E402
from labmeeting.settings import Settings  # noqa: E402
from labmeeting.storage import MeetingStore  # noqa: E402

MEETING_DATE = dt.date(2026, 10, 28)


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(
        openai_api_key="sk-test-openai-0000000000000000",
        anthropic_api_key="sk-ant-test-0000000000000000",
        notion_api_key="ntn_test0000000000000000",
        notion_meeting_database_id=f"https://www.notion.so/lab/{MEETING_DB.replace('-', '')}?v=0123456789abcdef0123456789abcdef",
        notion_ai_notes_database_id=AI_DB,
        notion_meeting_relation_property="AI 정리본",
        notion_meeting_date_property="날짜",
        recording_dir=tmp_path / "recordings",
        transcript_dir=tmp_path / "transcripts",
        minutes_dir=tmp_path / "minutes",
        log_dir=tmp_path / "logs",
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def store(settings) -> MeetingStore:
    return MeetingStore(settings.recording_dir)


@pytest.fixture
def fake_notion() -> FakeNotion:
    return FakeNotion()


@pytest.fixture
def notion_client(fake_notion) -> NotionClient:
    return NotionClient("ntn_test0000000000000000", transport=fake_notion.transport, sleep=lambda s: None, min_interval=0)


TRANSCRIPT = """[≈00:00:00] 오늘은 실험 계획 얘기부터 하죠.
[≈00:00:10] 민수 씨가 다음 주 금요일까지 실험 계획을 수정해 주세요.
[≈00:00:20] 실험 계획을 다음 주까지 수정해야 할 것 같습니다.
[≈00:00:30] 데이터 분석은 R 말고 Python 으로 하는 걸로 하죠.
"""


def sample_notes(**overrides) -> dict:
    """LLM 이 돌려줄 법한 회의록 JSON (스키마 준수)."""
    ev = lambda q, ts="≈00:00:10": {"quote": q, "is_paraphrase": False, "timestamp": ts}  # noqa: E731
    action = {
        "task_id": "x", "task": "실험 계획 수정", "purpose": "다음 실험 준비", "topic_id": "T1",
        "assignees": ["민수 씨"], "assignee_evidence": "민수 씨가 다음 주 금요일까지 실험 계획을 수정해 주세요.",
        "due_date": "2026-11-06", "due_date_text": "다음 주 금요일까지", "status": "open", "commitment": "confirmed",
        "basis": "explicit", "evidence": [ev("민수 씨가 다음 주 금요일까지 실험 계획을 수정해 주세요.")],
        "context": "", "confidence": 0.9, "needs_confirmation": False, "interpretation_notes": "",
    }
    notes = {
        "meeting_title": "261028(수) 회의", "meeting_date": "2026-10-28",
        "overview": "실험 계획과 분석 도구를 논의.", "executive_summary": ["실험 계획 수정 필요", "분석은 Python"],
        "topics": [{"topic_id": "T1", "title": "실험 계획", "context": "다음 실험 준비", "initial_question": "",
                    "hypotheses": [], "alternatives": [{"option": "R 사용", "arguments_for": [], "objections": ["유지보수"],
                                                       "state": "rejected", "evidence": []}],
                    "final_position": "Python 사용", "final_state": "confirmed", "remaining_uncertainties": [],
                    "evidence": [ev("데이터 분석은 R 말고 Python 으로 하는 걸로 하죠.", "≈00:00:30")]}],
        "reconstructed_reasoning": [{"topic_id": "T1", "narrative": "R 대신 Python 으로 정리됨."}],
        "decisions": [{"decision_id": "x", "topic_id": "T1", "statement": "분석은 Python", "state": "confirmed",
                       "basis": "explicit", "rationale": "", "interpretation_notes": "",
                       "evidence": [ev("데이터 분석은 R 말고 Python 으로 하는 걸로 하죠.", "≈00:00:30")]}],
        "revisions": [], "action_items": [action], "needs_clarification": [], "unresolved_questions": [],
        "follow_up_questions": [], "reference_resolutions": [], "key_excerpts": [], "quality_checks": [],
    }
    notes.update(overrides)
    return notes


class FakeProvider:
    """미리 정한 응답을 차례로 돌려주는 가짜 LLM."""

    def __init__(self, responses: list[str], model: str = "claude-opus-5-5"):
        self.responses = list(responses)
        self.model = model
        self.calls = 0

    def generate_json(self, system, user, schema):
        self.calls += 1
        return LLMResult(text=self.responses.pop(0), model=self.model, input_tokens=1000, output_tokens=500)


class FakeTranscriber:
    model = "gpt-transcribe"

    def __init__(self, text_fn=None, fail_at: int | None = None):
        self.calls = 0
        self.text_fn = text_fn or (lambda i: f"조각 {i} 발언입니다.")
        self.fail_at = fail_at

    def transcribe(self, path, prompt):
        from labmeeting.transcription import TranscriptionError

        self.calls += 1
        if self.fail_at is not None and self.calls == self.fail_at:
            raise TranscriptionError("네트워크 오류 (테스트)")
        idx = int(Path(path).stem.split("_")[1])
        return {"text": self.text_fn(idx), "segments": None, "usage": None}


def write_tone(path: Path, seconds: float, sr: int = 16000) -> Path:
    t = np.arange(int(seconds * sr)) / sr
    sf.write(str(path), (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16), sr, subtype="PCM_16", format="FLAC")
    return path


def notes_json(**overrides) -> str:
    return json.dumps(sample_notes(**overrides), ensure_ascii=False)
