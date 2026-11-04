"""앱 실행기: 서버를 켜고, 준비되면 브라우저를 직접 연다 (start-mac.command / start-windows.bat 이 호출).

Streamlit 의 자체 브라우저 열기는 첫 실행 때 터미널에서 이메일 입력을 기다리며 멈출 수 있어,
서버는 headless 로 띄우고 브라우저는 여기서 연다. 이미 실행 중이면 브라우저만 다시 연다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("LAB_MEETING_PORT", "8501"))
URL = f"http://localhost:{PORT}"


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/_stcore/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    if healthy():
        print(f"이미 실행 중입니다. 브라우저를 엽니다: {URL}")
        webbrowser.open(URL)
        return 0

    print("서버를 시작합니다...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
         "--server.headless", "true", "--server.showEmailPrompt", "false",
         "--server.address", "127.0.0.1", "--server.port", str(PORT),
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT,
    )
    try:
        for _ in range(240):  # 최대 2분 대기
            if proc.poll() is not None:
                print(f"\n서버가 시작되지 못했습니다 (종료 코드 {proc.returncode}). "
                      f"다른 프로그램이 {PORT}번 포트를 쓰고 있지 않은지 확인하세요.")
                return 1
            if healthy():
                break
            time.sleep(0.5)
        else:
            print(f"\n서버 준비가 늦어지고 있습니다. 잠시 후 브라우저에서 {URL} 을 직접 열어 주세요.")
        print(f"\n✅ 준비 완료: {URL}")
        print("   브라우저가 열리지 않으면 위 주소를 브라우저 주소창에 붙여 넣으세요.")
        print("   이 창을 닫으면 프로그램과 녹음이 종료됩니다. 회의 중에는 닫지 마세요.\n")
        webbrowser.open(URL)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
