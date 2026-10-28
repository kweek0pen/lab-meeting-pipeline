"""녹음 중 노트북이 잠자기로 들어가 녹음이 끊기지 않도록 막는다 (화면 꺼짐은 허용).

- macOS: caffeinate -i
- Windows: SetThreadExecutionState 는 '호출한 스레드'에만 적용되므로 전용 스레드에서 주기적으로 호출
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading


class KeepAwake:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            if sys.platform == "darwin":
                self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            elif sys.platform == "win32":
                self._stop.clear()
                self._thread = threading.Thread(target=self._win_loop, daemon=True)
                self._thread.start()
        except Exception:
            pass  # 실패해도 녹음은 계속

    def _win_loop(self) -> None:
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        while not self._stop.is_set():
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            self._stop.wait(30)
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    def stop(self) -> None:
        try:
            if self._proc:
                self._proc.terminate()
                self._proc = None
            self._stop.set()
        except Exception:
            pass
