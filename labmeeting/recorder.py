"""마이크 녹음.

크래시 안전 설계:
- 녹음 중에는 압축하지 않은 PCM(16bit) 바이트를 segment_000.pcm, segment_001.pcm … 에 '이어 붙이기'만 한다.
  헤더가 없으므로 프로그램이 갑자기 꺼져도 그 순간까지의 소리는 그대로 남는다.
- 2초마다 flush + fsync 로 디스크에 확실히 기록한다.
- 녹음 종료(또는 다음 실행 시 복구)에서 PCM → recording.flac(무손실)으로 합치고,
  길이가 일치하는지 검증한 뒤에만 임시 PCM 을 지운다. 원본 소리는 FLAC 으로 그대로 보존된다.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .keepawake import KeepAwake

log = logging.getLogger("labmeeting.recorder")

SEGMENT_SECONDS = 300
FLUSH_INTERVAL = 2.0
PCM_INFO = "recording_info.json"
FINAL_NAME = "recording.flac"


class RecorderError(RuntimeError):
    pass


def list_input_devices() -> list[dict]:
    import sounddevice as sd

    try:
        default_in = sd.default.device[0]
        devices = []
        for idx, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                devices.append({"index": idx, "name": d["name"], "default": idx == default_in,
                                "samplerate": d["default_samplerate"]})
        return devices
    except Exception as e:  # PortAudio 초기화 실패 등
        log.warning("입력 장치 조회 실패: %s", e)
        return []


def _pick_samplerate(device: int | None, preferred: int) -> int:
    import sounddevice as sd

    try:
        sd.check_input_settings(device=device, samplerate=preferred, channels=1, dtype="int16")
        return preferred
    except Exception:
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        return int(info["default_samplerate"])


def _default_stream_factory(**kwargs):
    import sounddevice as sd

    return sd.InputStream(**kwargs)


class Recorder:
    """하나의 회의 녹음을 담당. start() → stop() 순서로 사용."""

    def __init__(self, out_dir: Path, device: int | None = None, samplerate: int = 16000,
                 stream_factory: Callable | None = None, segment_seconds: int = SEGMENT_SECONDS,
                 keep_awake: bool = True):
        self.out_dir = Path(out_dir)
        self.device = device
        self.preferred_samplerate = samplerate
        self.samplerate = samplerate
        self.stream_factory = stream_factory or _default_stream_factory
        self.segment_seconds = segment_seconds
        self.keep_awake = KeepAwake() if keep_awake else None
        self._queue: queue.Queue = queue.Queue()
        self._writer: threading.Thread | None = None
        self._stream = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self.frames_written = 0
        self.level = 0.0
        self.peak_seen = 0
        self.error: str | None = None
        self.warnings: list[str] = []

    # ---------- 상태 ----------
    @property
    def running(self) -> bool:
        return self._started_at is not None and self._stopped_at is None

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return (self._stopped_at or time.time()) - self._started_at

    def silence_suspected(self) -> bool:
        """5초 넘게 완전한 무음이면 마이크 권한 거부를 의심."""
        return self.running and self.elapsed > 5 and self.peak_seen == 0

    # ---------- 동작 ----------
    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self.stream_factory is _default_stream_factory:
            self.samplerate = _pick_samplerate(self.device, self.preferred_samplerate)
        info = {"samplerate": self.samplerate, "channels": 1, "dtype": "int16",
                "started_at": time.time(), "device": self.device}
        (self.out_dir / PCM_INFO).write_text(json.dumps(info), encoding="utf-8")
        self._writer = threading.Thread(target=self._write_loop, name="recorder-writer", daemon=True)
        self._writer.start()
        try:
            self._stream = self.stream_factory(
                device=self.device, samplerate=self.samplerate, channels=1, dtype="int16",
                callback=self._callback, finished_callback=self._on_finished,
            )
            self._stream.start()
        except Exception as e:
            self._queue.put(None)
            self._writer.join(timeout=5)
            raise RecorderError(
                "마이크를 열 수 없습니다. 마이크가 연결되어 있는지, 그리고 운영체제 설정에서 "
                "이 프로그램(터미널/명령 프롬프트)의 마이크 접근을 허용했는지 확인하세요. "
                f"(상세: {e})"
            ) from e
        self._started_at = time.time()
        if self.keep_awake:
            self.keep_awake.start()
        log.info("녹음 시작: %s (samplerate=%s)", self.out_dir.name, self.samplerate)

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.warning("오디오 상태: %s", status)
        data = np.asarray(indata, dtype=np.int16).reshape(-1)
        self._queue.put(data.tobytes())
        if data.size:
            peak = int(np.abs(data.astype(np.int32)).max())
            self.peak_seen = max(self.peak_seen, peak)
            self.level = min(1.0, float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) / 8000.0)

    def _on_finished(self) -> None:
        if self.running:
            self.error = ("녹음 장치 연결이 끊겼거나 스트림이 중단되었습니다. "
                          "지금까지 녹음된 내용은 저장되어 있습니다. 'Stop Meeting'을 눌러 처리를 진행하세요.")
            log.error("오디오 스트림이 예기치 않게 종료됨")

    def _write_loop(self) -> None:
        bytes_per_segment = self.segment_seconds * self.samplerate * 2
        seg_index, seg_bytes = 0, 0
        f = open(self.out_dir / f"segment_{seg_index:03d}.pcm", "ab")
        last_flush = time.time()
        try:
            while True:
                try:
                    chunk = self._queue.get(timeout=0.5)
                except queue.Empty:
                    chunk = b""
                if chunk is None:
                    break
                if chunk:
                    f.write(chunk)
                    seg_bytes += len(chunk)
                    self.frames_written += len(chunk) // 2
                if time.time() - last_flush > FLUSH_INTERVAL:
                    f.flush()
                    os.fsync(f.fileno())
                    last_flush = time.time()
                if seg_bytes >= bytes_per_segment:
                    f.flush()
                    os.fsync(f.fileno())
                    f.close()
                    seg_index, seg_bytes = seg_index + 1, 0
                    f = open(self.out_dir / f"segment_{seg_index:03d}.pcm", "ab")
        except OSError as e:
            self.error = f"녹음 파일 저장 실패 (디스크 공간/권한 확인): {e}"
            log.exception("녹음 파일 쓰기 실패")
        finally:
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except OSError:
                pass

    def stop(self, finalize: bool = True) -> Path | None:
        """녹음을 멈춘다. finalize=True 면 FLAC 으로 합친 최종 파일 경로를 돌려준다
        (앱에서는 False 로 멈추고, 합치기는 처리 파이프라인의 'Saving audio' 단계가 맡는다)."""
        self._stopped_at = time.time()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception as e:
            log.warning("스트림 종료 중 오류(무시): %s", e)
        finally:
            if self.keep_awake:
                self.keep_awake.stop()
        self._queue.put(None)
        if self._writer:
            self._writer.join(timeout=30)
        log.info("녹음 종료: %.1f초", self.elapsed)
        return finalize_recording(self.out_dir) if finalize else None


def has_unfinalized_pcm(meeting_dir: Path) -> bool:
    return any(Path(meeting_dir).glob("segment_*.pcm"))


def finalize_recording(meeting_dir: Path) -> Path:
    """segment_*.pcm → recording.flac. 검증 후 PCM 삭제. 여러 번 호출해도 안전."""
    meeting_dir = Path(meeting_dir)
    final = meeting_dir / FINAL_NAME
    segments = sorted(meeting_dir.glob("segment_*.pcm"))
    if not segments:
        if final.exists():
            return final
        raise RecorderError("녹음 데이터가 없습니다.")
    info = json.loads((meeting_dir / PCM_INFO).read_text(encoding="utf-8"))
    sr = int(info["samplerate"])
    total_frames = sum(p.stat().st_size // 2 for p in segments)
    tmp = meeting_dir / (FINAL_NAME + ".partial")
    with sf.SoundFile(tmp, mode="w", samplerate=sr, channels=1, subtype="PCM_16", format="FLAC") as out:
        for seg in segments:
            raw = seg.read_bytes()
            raw = raw[: len(raw) // 2 * 2]  # 중간에 끊긴 마지막 바이트 제거
            out.write(np.frombuffer(raw, dtype=np.int16))
    if sf.info(str(tmp)).frames != total_frames:
        raise RecorderError("녹음 파일 변환 검증 실패. 원본 PCM 파일은 보존했습니다.")
    os.replace(tmp, final)
    for seg in segments:
        seg.unlink()
    log.info("녹음 파일 저장 완료: %s (%.1f초)", final, total_frames / sr)
    return final


def audio_duration(path: Path) -> float:
    i = sf.info(str(path))
    return i.frames / i.samplerate
