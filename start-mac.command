#!/bin/bash
# 더블클릭으로 실행 (macOS). 처음 한 번은 필요한 도구를 자동 설치합니다(수 분 소요).
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "▶ 처음 실행: 파이썬 실행 도구(uv)를 설치합니다..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || { echo "설치 실패. 인터넷 연결을 확인하세요."; read -r -p "Enter 를 누르면 닫힙니다"; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi

[ -f .env ] || cp .env.example .env

echo "▶ 프로그램을 시작합니다. 잠시 후 브라우저가 열립니다."
echo "  (이 창을 닫으면 프로그램과 녹음이 종료됩니다. 회의 중에는 닫지 마세요.)"
uv run --quiet streamlit run app.py --server.address 127.0.0.1 --server.port 8501 \
  --browser.gatherUsageStats false --server.headless false
read -r -p "프로그램이 종료되었습니다. Enter 를 누르면 창이 닫힙니다."
