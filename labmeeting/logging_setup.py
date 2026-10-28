"""로그 설정. API 키·토큰·인증 헤더가 로그에 남지 않도록 모든 로그를 가린다."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # OpenAI / Anthropic
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),  # Notion (신규 형식)
    re.compile(r"secret_[A-Za-z0-9]{8,}"),  # Notion (구 형식)
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]+"),
]


def redact(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = list(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        record.msg = redact(message, self.secrets)
        record.args = ()
        return True


def setup_logging(log_dir: Path, secrets: list[str]) -> logging.Logger:
    logger = logging.getLogger("labmeeting")
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(secrets))
    logger.addHandler(handler)
    logger.propagate = False
    # HTTP 라이브러리 내부 로그(요청 헤더 포함 가능)는 남기지 않는다
    for noisy in ("httpx", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logger
