"""환경설정(.env) 로딩.

모든 모델명·DB ID·속성 이름은 여기서만 읽는다. 코드 다른 곳에 하드코딩하지 않는다.
.env 는 화면의 '설정' 탭에서 수정할 수 있고, 수정 즉시 다시 읽는다.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"

SECRET_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NOTION_API_KEY")

# 기본값. 비어 있는 .env 항목은 이 값으로 채운다.
DEFAULTS: dict[str, str] = {
    "TRANSCRIPTION_PROVIDER": "openai",
    "TRANSCRIPTION_MODEL": "gpt-transcribe",
    "TRANSCRIPTION_LANGUAGE": "ko",
    "TRANSCRIPTION_CHUNK_SECONDS": "180",
    "TRANSCRIPTION_OVERLAP_SECONDS": "8",
    "SUMMARY_PROVIDER": "",  # 비우면: Anthropic 키가 있으면 anthropic, 없으면 openai
    "SUMMARY_MODEL": "",  # 비우면 제공자별 기본 모델
    "SUMMARY_EFFORT": "high",
    "ANTHROPIC_DEFAULT_MODEL": "claude-opus-5-5",
    "OPENAI_DEFAULT_SUMMARY_MODEL": "gpt-6.1-sol",
    "RECORDING_SAMPLE_RATE": "16000",
    "RETENTION_DAYS": "0",  # 0 = 원본 녹음 무기한 보존
    "GLOSSARY": "",
}


@dataclass
class Settings:
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    notion_api_key: str = ""
    notion_meeting_database_id: str = ""
    notion_ai_notes_database_id: str = ""
    notion_meeting_relation_property: str = ""
    notion_ai_notes_relation_property: str = ""  # 선택: 양방향 동기화가 안 될 때만 필요
    notion_meeting_date_property: str = ""  # 선택
    notion_ai_notes_date_property: str = ""  # 선택
    notion_meeting_project_relation_property: str = ""  # 선택
    transcription_provider: str = "openai"
    transcription_model: str = "gpt-transcribe"
    transcription_language: str = "ko"
    transcription_chunk_seconds: int = 180
    transcription_overlap_seconds: int = 8
    summary_provider: str = "anthropic"
    summary_model: str = "claude-opus-5-5"
    summary_effort: str = "high"
    recording_sample_rate: int = 16000
    retention_days: int = 0
    glossary: str = ""
    recording_dir: Path = ROOT / "recordings"
    transcript_dir: Path = ROOT / "transcripts"
    minutes_dir: Path = ROOT / "minutes"
    log_dir: Path = ROOT / "logs"
    secrets: list[str] = field(default_factory=list)

    def ensure_dirs(self) -> None:
        for d in (self.recording_dir, self.transcript_dir, self.minutes_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ---- 단계별로 무엇이 빠졌는지 안내 (앱이 죽지 않도록) ----
    def missing_for_transcription(self) -> list[str]:
        return [] if self.openai_api_key else ["OPENAI_API_KEY (전사용)"]

    def missing_for_summary(self) -> list[str]:
        if self.summary_provider == "anthropic" and not self.anthropic_api_key:
            return ["ANTHROPIC_API_KEY (회의록 생성용)"]
        if self.summary_provider == "openai" and not self.openai_api_key:
            return ["OPENAI_API_KEY (회의록 생성용)"]
        return []

    def missing_for_notion(self) -> list[str]:
        need = {
            "NOTION_API_KEY": self.notion_api_key,
            "NOTION_MEETING_DATABASE_ID": self.notion_meeting_database_id,
            "NOTION_AI_NOTES_DATABASE_ID": self.notion_ai_notes_database_id,
            "NOTION_MEETING_RELATION_PROPERTY": self.notion_meeting_relation_property,
        }
        return [k for k, v in need.items() if not v]

    def missing_all(self) -> list[str]:
        return self.missing_for_transcription() + self.missing_for_summary() + self.missing_for_notion()


def ensure_env_file() -> None:
    """처음 실행 시 .env.example 을 복사해 .env 를 만든다."""
    if not ENV_PATH.exists() and ENV_EXAMPLE_PATH.exists():
        shutil.copy(ENV_EXAMPLE_PATH, ENV_PATH)


def _raw_values() -> dict[str, str]:
    values = dict(DEFAULTS)
    file_values = {k: (v or "") for k, v in dotenv_values(ENV_PATH).items()} if ENV_PATH.exists() else {}
    for key, value in file_values.items():
        if value.strip():
            values[key] = value.strip()
    # 실제 환경변수가 있으면 우선 (CI, 고급 사용자)
    for key in set(values) | set(file_values) | {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NOTION_API_KEY"}:
        if os.environ.get(key):
            values[key] = os.environ[key].strip()
    return values


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_settings() -> Settings:
    v = _raw_values()
    s = Settings(
        openai_api_key=v.get("OPENAI_API_KEY", ""),
        anthropic_api_key=v.get("ANTHROPIC_API_KEY", ""),
        notion_api_key=v.get("NOTION_API_KEY", ""),
        notion_meeting_database_id=v.get("NOTION_MEETING_DATABASE_ID", ""),
        notion_ai_notes_database_id=v.get("NOTION_AI_NOTES_DATABASE_ID", ""),
        notion_meeting_relation_property=v.get("NOTION_MEETING_RELATION_PROPERTY", ""),
        notion_ai_notes_relation_property=v.get("NOTION_AI_NOTES_RELATION_PROPERTY", ""),
        notion_meeting_date_property=v.get("NOTION_MEETING_DATE_PROPERTY", ""),
        notion_ai_notes_date_property=v.get("NOTION_AI_NOTES_DATE_PROPERTY", ""),
        notion_meeting_project_relation_property=v.get("NOTION_MEETING_PROJECT_RELATION_PROPERTY", ""),
        transcription_provider=v["TRANSCRIPTION_PROVIDER"],
        transcription_model=v["TRANSCRIPTION_MODEL"],
        transcription_language=v["TRANSCRIPTION_LANGUAGE"],
        transcription_chunk_seconds=_int(v["TRANSCRIPTION_CHUNK_SECONDS"], 180),
        transcription_overlap_seconds=_int(v["TRANSCRIPTION_OVERLAP_SECONDS"], 8),
        summary_effort=v["SUMMARY_EFFORT"],
        recording_sample_rate=_int(v["RECORDING_SAMPLE_RATE"], 16000),
        retention_days=_int(v["RETENTION_DAYS"], 0),
        glossary=v.get("GLOSSARY", ""),
    )
    provider = v.get("SUMMARY_PROVIDER", "").lower()
    if provider not in ("anthropic", "openai"):
        provider = "anthropic" if s.anthropic_api_key or not s.openai_api_key else "openai"
    s.summary_provider = provider
    s.summary_model = v.get("SUMMARY_MODEL") or (
        v["ANTHROPIC_DEFAULT_MODEL"] if provider == "anthropic" else v["OPENAI_DEFAULT_SUMMARY_MODEL"]
    )
    for env_key, attr in (
        ("RECORDING_DIR", "recording_dir"),
        ("TRANSCRIPT_DIR", "transcript_dir"),
        ("MINUTES_DIR", "minutes_dir"),
        ("LOG_DIR", "log_dir"),
    ):
        if v.get(env_key):
            setattr(s, attr, Path(v[env_key]).expanduser())
    s.secrets = [v.get(k, "") for k in SECRET_VARS if v.get(k)]
    return s


def update_env(updates: dict[str, str]) -> None:
    """설정 화면에서 받은 값을 .env 에 저장. 빈 문자열이 아닌 비밀값만 덮어쓴다."""
    ensure_env_file()
    ENV_PATH.touch(exist_ok=True)
    for key, value in updates.items():
        value = (value or "").strip()
        if key in SECRET_VARS and not value:
            continue  # 비밀값 입력칸을 비워 두면 기존 값 유지
        set_key(str(ENV_PATH), key, value, quote_mode="never")


def mask(secret: str) -> str:
    return f"••••{secret[-4:]}" if secret else ""
