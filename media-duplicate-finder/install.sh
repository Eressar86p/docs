#!/usr/bin/env bash
set -euo pipefail

echo "=== Media Duplicate Finder - Installer ==="
echo ""

# ---------- Check Python 3 ----------
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 is not installed."
    echo "  Install it with:  sudo apt install python3 python3-pip python3-tk"
    exit 1
fi

PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[OK] Found Python ${PYVER}"

# ---------- Check tkinter ----------
if ! python3 -c "import tkinter" &>/dev/null; then
    echo "[ERROR] python3-tkinter is not installed."
    echo "  Install it with:  sudo apt install python3-tk"
    exit 1
fi
echo "[OK] tkinter available"

# ---------- Check ffprobe ----------
if ! command -v ffprobe &>/dev/null; then
    echo "[WARNING] ffprobe is not installed. Media metadata extraction will be limited."
    echo "  Install it with:  sudo apt install ffmpeg"
else
    echo "[OK] ffprobe available"
fi

# ---------- Create virtual-env ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

if [ ! -d "${VENV_DIR}" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"

echo "Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "${SCRIPT_DIR}/requirements.txt"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Run the program with:"
echo "  cd ${SCRIPT_DIR}"
echo "  source .venv/bin/activate"
echo "  python3 main.py"
echo ""
echo "Or simply:"
echo "  ${SCRIPT_DIR}/.venv/bin/python3 ${SCRIPT_DIR}/main.py"
