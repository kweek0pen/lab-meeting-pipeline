#!/bin/bash
# macOS 설치/업데이트 스크립트.
# 터미널에 아래 한 줄을 붙여 넣어 실행합니다:
#   curl -fsSL https://raw.githubusercontent.com/kweek0pen/lab-meeting-pipeline/main/install-mac.sh | bash
#
# 브라우저로 받은 ZIP 은 macOS 가 '신뢰할 수 없는 개발자' 로 막지만, 이렇게 터미널로 받으면 막히지 않습니다.
# 다시 실행하면 프로그램만 최신으로 바뀌고 설정(.env)과 녹음·회의록은 그대로 남습니다.
set -euo pipefail

REPO="kweek0pen/lab-meeting-pipeline"
DEST="$HOME/lab-meeting-pipeline"
APP="$HOME/Desktop/회의록 자동화.app"

echo "▶ 프로그램을 내려받습니다..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.zip" -o "$TMP/app.zip"
unzip -q "$TMP/app.zip" -d "$TMP"

mkdir -p "$DEST"
# 기존 파일 위에 덮어쓰기만 하고 지우지 않음 → .env, recordings/, transcripts/, minutes/ 보존
ditto "$TMP/lab-meeting-pipeline-main" "$DEST"
chmod +x "$DEST/start-mac.command" "$DEST/install-mac.sh"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

echo "▶ 바탕화면에 '회의록 자동화' 앱을 만듭니다..."
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"open -a Terminal \" & quoted form of \"$DEST/start-mac.command\""

echo ""
echo "✅ 설치 완료: $DEST"
echo "   바탕화면의 '회의록 자동화' 를 더블클릭해 실행하세요."
echo "   (처음 실행은 필요한 파일을 내려받느라 2~5분 걸립니다.)"
open -a Terminal "$DEST/start-mac.command"
