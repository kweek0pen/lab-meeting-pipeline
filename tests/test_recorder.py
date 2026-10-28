import threading
import time

import numpy as np
import pytest
import soundfile as sf

from labmeeting.recorder import Recorder, RecorderError, finalize_recording, has_unfinalized_pcm


class FakeStream:
    """sounddevice.InputStream 흉내: 별도 스레드에서 콜백으로 사인파를 보낸다."""

    def __init__(self, *, samplerate, callback, finished_callback=None, blocksize=1600, **kw):
        self.sr, self.cb, self.finished = samplerate, callback, finished_callback
        self.block = blocksize
        self._stop = threading.Event()
        self.sent = 0

    def start(self):
        def run():
            t0 = 0
            while not self._stop.is_set():
                t = (np.arange(self.block) + t0) / self.sr
                data = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16).reshape(-1, 1)
                self.cb(data, self.block, None, None)
                self.sent += self.block
                t0 += self.block
                time.sleep(0.002)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def close(self):
        pass


# 12. 녹음 파일이 정상적으로 저장됨
def test_recording_saved_as_flac(tmp_path):
    streams = []

    def factory(**kw):
        streams.append(FakeStream(**kw))
        return streams[0]

    rec = Recorder(tmp_path / "m1", stream_factory=factory, samplerate=16000, segment_seconds=1, keep_awake=False)
    rec.start()
    time.sleep(0.5)
    path = rec.stop()
    info = sf.info(str(path))
    assert path.name == "recording.flac" and info.samplerate == 16000
    assert info.frames == streams[0].sent  # 보낸 샘플이 하나도 빠지지 않음
    assert not has_unfinalized_pcm(tmp_path / "m1")  # 임시 PCM 정리
    assert rec.peak_seen > 0


def test_crash_recovery_from_pcm(tmp_path):
    """녹음 중 프로그램이 꺼진 상황: PCM 조각만 남아 있어도 복구된다."""
    rec = Recorder(tmp_path / "m2", stream_factory=lambda **kw: FakeStream(**kw), segment_seconds=1, keep_awake=False)
    rec.start()
    time.sleep(0.3)
    rec._stream.stop()  # stop() 을 부르지 않고 '강제 종료' 흉내
    rec._queue.put(None)
    rec._writer.join()
    # 마지막 바이트가 반쯤 쓰인 상황도 흉내
    with open(sorted((tmp_path / "m2").glob("segment_*.pcm"))[-1], "ab") as f:
        f.write(b"\x01")
    assert has_unfinalized_pcm(tmp_path / "m2")
    path = finalize_recording(tmp_path / "m2")
    assert sf.info(str(path)).frames > 0
    assert finalize_recording(tmp_path / "m2") == path  # 두 번 불러도 안전


def test_microphone_open_failure_message(tmp_path):
    def broken(**kw):
        raise OSError("PortAudio: Error opening InputStream")

    rec = Recorder(tmp_path / "m3", stream_factory=broken, keep_awake=False)
    with pytest.raises(RecorderError, match="마이크"):
        rec.start()


def test_device_disconnect_sets_error(tmp_path):
    rec = Recorder(tmp_path / "m4", stream_factory=lambda **kw: FakeStream(**kw), keep_awake=False)
    rec.start()
    rec._on_finished()  # 장치 분리 시 sounddevice 가 호출
    assert rec.error and "저장되어 있습니다" in rec.error
    rec.stop()
