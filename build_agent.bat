@echo off
echo === AI-LogOps Agent Build ===
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH
    exit /b 1
)

REM Install build dependencies
python -m pip install pyinstaller

REM Clean previous build
if exist dist\AILogOps-Agent rmdir /S /Q dist\AILogOps-Agent
if exist build\AILogOps-Agent rmdir /S /Q build\AILogOps-Agent

REM Build
python -m PyInstaller agent.spec --noconfirm

REM Create runtime directories
mkdir dist\AILogOps-Agent\backups 2>nul
mkdir dist\AILogOps-Agent\temp 2>nul
mkdir dist\AILogOps-Agent\storage 2>nul

echo.
echo === Build Complete ===
echo Output: dist\AILogOps-Agent\
echo.
echo Next steps:
echo   1. Edit dist\AILogOps-Agent\config.yaml
echo   2. Run as admin: dist\AILogOps-Agent\AILogOps-Agent.exe install
echo   3. Start service: dist\AILogOps-Agent\AILogOps-Agent.exe start
