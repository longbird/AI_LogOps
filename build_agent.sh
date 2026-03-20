#!/bin/bash
echo "=== AI-LogOps Agent Build (macOS) ==="
echo

# Check Python
python3 --version >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "[ERROR] Python3 not found in PATH"
    exit 1
fi

# Install build dependencies
python3 -m pip install pyinstaller

# Clean previous build
rm -rf dist/AILogOps-Agent build/AILogOps-Agent

# Build
python3 -m PyInstaller agent_mac.spec --noconfirm

# Create runtime directories
mkdir -p dist/AILogOps-Agent/backups
mkdir -p dist/AILogOps-Agent/temp
mkdir -p dist/AILogOps-Agent/storage
mkdir -p dist/AILogOps-Agent/log

echo
echo "=== Build Complete ==="
echo "Output: dist/AILogOps-Agent/"
echo
echo "Next steps:"
echo "  1. Edit dist/AILogOps-Agent/config.yaml"
echo "  2. Run: dist/AILogOps-Agent/AILogOps-Agent install"
echo "  3. Or run directly: dist/AILogOps-Agent/AILogOps-Agent"
echo "  4. GUI mode: dist/AILogOps-Agent/AILogOps-Agent gui"
