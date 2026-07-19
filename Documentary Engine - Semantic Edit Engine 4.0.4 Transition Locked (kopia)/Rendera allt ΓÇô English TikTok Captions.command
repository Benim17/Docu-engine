#!/bin/bash
set -u
cd "$(dirname "$0")"
export LC_ALL=C
export LANG=C
LOG="render_motion_301.log"
exec > >(tee "$LOG") 2>&1

echo "=== Documentary Engine v2 – Semantic Edit Engine 4.0 + Motion Engine 3.0.4 ==="

pause_and_exit() {
  local status="$1"
  read -r -p "Tryck Enter för att stänga …"
  exit "$status"
}

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew saknas. Installera Homebrew från brew.sh och kör igen."
  pause_and_exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Installerar FFmpeg …"
  brew install ffmpeg || pause_and_exit 1
fi

PYTHON_BIN=""
for candidate in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3.12 /usr/local/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "Installerar Python 3.12 …"
  brew install python@3.12 || pause_and_exit 1
  PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12"
fi

if [ -d ".venv" ] && ! .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
  echo "Tar bort en gammal eller flyttad Python-miljö …"
  rm -rf .venv
fi
if [ ! -d ".venv" ]; then
  echo "Skapar Python-miljö …"
  "$PYTHON_BIN" -m venv .venv || pause_and_exit 1
fi

PY=".venv/bin/python"
"$PY" -m pip install --upgrade pip || pause_and_exit 1
"$PY" -m pip install "mlx-whisper==0.4.3" "Pillow>=10,<13" "opencv-python-headless>=4.10,<5" "numpy>=1.26,<3" || pause_and_exit 1

echo "Förbereder projektet …"
"$PY" prepare_project.py || pause_and_exit 1

echo "Startar hela pipeline: transkribering → semantisk bildmatchning → Motion Engine → rendering …"
"$PY" -m engine.pipeline
STATUS=$?

if [ "$STATUS" -eq 0 ]; then
  echo
  echo "Allt lyckades. Öppnar output-mappen."
  open output
else
  echo
  echo "Renderingen misslyckades. Skicka render_motion_301.log till ChatGPT."
fi
pause_and_exit "$STATUS"
