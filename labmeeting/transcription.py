"""음성 → 텍스트 전사.

- OpenAI 전사 API 파일 제한(25MB, wav/mp3/m4a/webm 등)에 맞춰 녹음을 WAV 조각으로 나눈다.
- 조각 사이에 겹침 구간(기본 8초)을 두어 경계에서 말이 잘리지 않게 하고, 합칠 때 중복을 제거한다.
- 조각별 결과를 바로 저장하므로 중간에 실패해도 다시 실행하면 남은 조각만 처리한다.
- 원문을 '정리'하거나 고쳐 쓰지 않는다. 전사 결과는 들은 그대로 보존한다(겹침 중복 제거만 수행).

타임스탬프:
- whisper-1 / *diarize 모델: API 가 구간별 시작·끝 시간을 준다 → 정확한 타임스탬프.
- gpt-transcribe 등: 텍스트만 준다 → 조각 시작 시각 + 글자 위치 비례로 계산한 '추정' 타임스탬프(≈ 표시).
화자 분리:
- *diarize 모델에서만 '화자 A/B' 라벨을 붙인다. 라벨은 실제 인물이 누구인지 의미하지 않으며, 추측하지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
import string
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

import soundfile as sf

from .storage import atomic_write_json, atomic_write_text
from .titles import fmt_ts

log = logging.getLogger("labmeeting.transcription")

MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # 공식 제한 25MB 보다 여유를 둠
PROMPT_CONTEXT_CHARS = 300


class TranscriptionError(RuntimeError):
    pass


@dataclass
class Chunk:
    index: int
    start: float
    end: float
    path: Path


# ---------------------------------------------------------------- 조각 나누기
def plan_chunks(duration: float, chunk_seconds: float, overlap_seconds: float) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    overlap_seconds = min(overlap_seconds, chunk_seconds / 4)
    step = chunk_seconds - overlap_seconds
    spans, start = [], 0.0
    while True:
        end = min(start + chunk_seconds, duration)
        spans.append((start, end))
        if end >= duration:
            break
        start += step
        if duration - start <= overlap_seconds + 0.5:  # 남은 부분이 겹침 구간뿐이면 끝
            break
    return spans


def write_chunks(audio_path: Path, chunks_dir: Path, chunk_seconds: float, overlap_seconds: float) -> list[Chunk]:
    info = sf.info(str(audio_path))
    sr = info.samplerate
    max_seconds = MAX_UPLOAD_BYTES / (sr * 2 * info.channels) - 1
    chunk_seconds = min(chunk_seconds, max_seconds)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    for i, (start, end) in enumerate(plan_chunks(info.frames / sr, chunk_seconds, overlap_seconds)):
        path = chunks_dir / f"chunk_{i:03d}.wav"
        if not path.exists():
            data, _ = sf.read(str(audio_path), start=int(start * sr), stop=int(end * sr), dtype="int16")
            sf.write(str(path), data, sr, subtype="PCM_16", format="WAV")
        chunks.append(Chunk(i, start, end, path))
    return chunks


# ---------------------------------------------------------------- 중복 제거
_PUNCT = string.punctuation + "“”‘’…·,.?!"


def _norm(word: str) -> str:
    return word.strip(_PUNCT).lower()


def dedupe_overlap(prev_text: str, next_text: str, window: int = 60, min_match: int = 3) -> str:
    """앞 조각 끝부분과 다음 조각 앞부분에서 겹치는 단어열을 찾아 다음 조각에서 제거."""
    prev_words, next_words = prev_text.split(), next_text.split()
    a = [_norm(w) for w in prev_words[-window:]]
    b = [_norm(w) for w in next_words[:window]]
    if not a or not b:
        return next_text
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    # 겹침은 앞 조각 '끝'과 다음 조각 '앞'에서만 일어난다
    if m.size >= min_match and m.a + m.size >= len(a) - 8 and m.b <= 20:
        return " ".join(next_words[m.b + m.size:])
    return next_text


_SENT_SPLIT = re.compile(r"(?<=[.?!。？！])\s+")


def _split_sentences(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SENT_SPLIT.split(text)) if s]


def merge_results(chunks: list[Chunk], results: list[dict], overlap_seconds: float) -> list[dict]:
    """조각별 결과를 하나의 구간 목록으로 합친다."""
    segments: list[dict] = []
    has_segments = all(r.get("segments") for r in results if r.get("text", "").strip())
    if has_segments:
        # 각 조각이 '소유'하는 시간 범위: 겹침 구간의 중간점을 경계로 사용
        for k, (chunk, res) in enumerate(zip(chunks, results)):
            lo = chunk.start + (overlap_seconds / 2 if k > 0 else 0)
            hi = chunks[k + 1].start + overlap_seconds / 2 if k + 1 < len(chunks) else float("inf")
            for seg in res.get("segments") or []:
                s, e = chunk.start + seg["start"], chunk.start + seg["end"]
                if lo <= (s + e) / 2 < hi and seg["text"].strip():
                    segments.append({"start": round(s, 2), "end": round(e, 2), "text": seg["text"].strip(),
                                     "speaker": seg.get("speaker"), "approx": False})
        return segments

    prev_text = ""
    for chunk, res in zip(chunks, results):
        text = res.get("text", "").strip()
        if prev_text:
            text = dedupe_overlap(prev_text, text)
        if not text:
            continue
        prev_text = res.get("text", "").strip()
        sentences = _split_sentences(text) or [text]
        total = sum(len(s) for s in sentences) or 1
        offset_lo = overlap_seconds / 2 if chunk.index > 0 else 0
        span = max(chunk.end - chunk.start - offset_lo, 1)
        pos = 0
        for sentence in sentences:
            ts = chunk.start + offset_lo + span * pos / total
            segments.append({"start": round(ts, 1), "end": None, "text": sentence, "speaker": None, "approx": True})
            pos += len(sentence)
    return segments


def render_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        mark = "≈" if seg.get("approx") else ""
        speaker = f"화자 {seg['speaker']}: " if seg.get("speaker") else ""
        lines.append(f"[{mark}{fmt_ts(seg['start'])}] {speaker}{seg['text']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- API 호출
class Transcriber(Protocol):
    model: str

    def transcribe(self, path: Path, prompt: str) -> dict: ...


class OpenAITranscriber:
    def __init__(self, api_key: str, model: str, language: str = "ko", client=None):
        from openai import OpenAI

        self.model = model
        self.language = language
        # SDK 가 429·5xx·네트워크 오류를 지수형 백오프로 자동 재시도
        self.client = client or OpenAI(api_key=api_key, max_retries=5, timeout=600)

    def transcribe(self, path: Path, prompt: str) -> dict:
        import openai

        kwargs: dict = {"model": self.model}
        if self.language:
            kwargs["language"] = self.language
        if "diarize" in self.model:
            kwargs.update(response_format="diarized_json", chunking_strategy="auto")  # prompt 미지원
        elif self.model == "whisper-1":
            kwargs.update(response_format="verbose_json", timestamp_granularities=["segment"],
                          prompt=prompt[-400:])  # whisper-1 prompt 는 224 토큰 제한
        else:
            kwargs.update(response_format="json", prompt=prompt)
        try:
            with open(path, "rb") as f:
                resp = self.client.audio.transcriptions.create(file=f, **kwargs)
        except openai.AuthenticationError as e:
            raise TranscriptionError("OpenAI API 키가 올바르지 않습니다. 설정에서 OPENAI_API_KEY를 확인하세요.") from e
        except openai.PermissionDeniedError as e:
            raise TranscriptionError(f"OpenAI 계정에 '{self.model}' 모델 사용 권한이 없습니다.") from e
        except openai.RateLimitError as e:
            if "quota" in str(e).lower():
                raise TranscriptionError(
                    "OpenAI API 크레딧이 없습니다. ChatGPT 구독과 API 요금은 별개이므로 "
                    "platform.openai.com 의 Billing 에서 결제 수단/크레딧을 등록하세요.") from e
            raise TranscriptionError("OpenAI 요청 한도 초과. 잠시 후 '이어서 처리'를 누르세요.") from e
        except openai.BadRequestError as e:
            raise TranscriptionError(f"전사 요청이 거부되었습니다: {e.message}") from e
        except openai.APIConnectionError as e:
            raise TranscriptionError("네트워크 오류로 전사하지 못했습니다. 인터넷 연결 후 '이어서 처리'를 누르세요.") from e
        except openai.APIStatusError as e:
            raise TranscriptionError(f"OpenAI 서버 오류({e.status_code}). 잠시 후 다시 시도하세요.") from e
        data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        segments = None
        if data.get("segments"):
            segments = [{"start": s["start"], "end": s["end"], "text": s["text"], "speaker": s.get("speaker")}
                        for s in data["segments"]]
        return {"text": data.get("text", ""), "segments": segments, "usage": data.get("usage")}


def build_prompt(glossary: str, attendees: str, previous_text: str) -> str:
    parts = ["한국어 연구실 회의 녹음입니다. 영어 전문 용어는 영어 원문 그대로 적어 주세요."]
    if glossary.strip():
        parts.append(f"용어: {glossary.strip()}")
    if attendees.strip():
        parts.append(f"참석자 이름: {attendees.strip()}")
    if previous_text:
        parts.append(previous_text[-PROMPT_CONTEXT_CHARS:])
    return "\n".join(parts)


def transcribe_recording(audio_path: Path, work_dir: Path, transcriber: Transcriber, *, chunk_seconds: float,
                         overlap_seconds: float, glossary: str = "", attendees: str = "",
                         progress=lambda done, total: None) -> dict:
    """녹음 전체를 전사해 {'segments','text','billed_seconds',...} 를 돌려준다. 조각 결과는 work_dir 에 캐시."""
    chunks = write_chunks(audio_path, work_dir / "chunks", chunk_seconds, overlap_seconds)
    if not chunks:
        raise TranscriptionError("녹음 길이가 0초입니다.")
    results, previous = [], ""
    for chunk in chunks:
        cache = chunk.path.with_suffix(".json")
        if cache.exists():
            result = json.loads(cache.read_text(encoding="utf-8"))
            if result.get("model") != transcriber.model:
                cache.unlink()
        if cache.exists():
            result = json.loads(cache.read_text(encoding="utf-8"))
        else:
            if chunk.end - chunk.start < 1.0:
                result = {"text": "", "segments": [], "usage": None}
            else:
                result = transcriber.transcribe(chunk.path, build_prompt(glossary, attendees, previous))
            result["model"] = transcriber.model
            atomic_write_json(cache, result)
        results.append(result)
        previous = result.get("text", "")
        progress(chunk.index + 1, len(chunks))
        log.info("전사 조각 %d/%d 완료", chunk.index + 1, len(chunks))

    segments = merge_results(chunks, results, min(overlap_seconds, chunk_seconds / 4))
    if not any(s["text"].strip() for s in segments):
        raise TranscriptionError("전사 결과가 비어 있습니다. 녹음에 말소리가 담겼는지(마이크 권한/음량) 확인하세요.")
    billed = sum(c.end - c.start for c in chunks)
    for chunk in chunks:  # 원본(FLAC)에서 언제든 다시 만들 수 있는 임시 WAV 만 삭제
        chunk.path.unlink(missing_ok=True)
    return {"segments": segments, "text": render_transcript(segments), "billed_seconds": billed,
            "chunk_count": len(chunks)}


def save_transcript(result: dict, *, meeting_id: str, model: str, language: str, txt_path: Path,
                    json_path: Path) -> None:
    diarized = any(s.get("speaker") for s in result["segments"])
    atomic_write_text(txt_path, result["text"])
    atomic_write_json(json_path, {
        "meeting_id": meeting_id,
        "model": model,
        "language": language,
        "timestamps": "approximate" if any(s.get("approx") for s in result["segments"]) else "segment",
        "diarization": diarized,
        "speaker_note": "화자 라벨(A, B …)은 실제 인물을 의미하지 않습니다." if diarized else None,
        "billed_seconds": result["billed_seconds"],
        "segments": result["segments"],
    })
