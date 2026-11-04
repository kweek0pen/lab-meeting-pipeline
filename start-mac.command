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

echo "▶ 프로그램을 준비합니다. 처음 실행은 필요한 파일을 내려받느라 2~5분 걸릴 수 있습니다."
uv run python launch.py
read -r -p "프로그램이 종료되었습니다. Enter 를 누르면 창이 닫힙니다."
