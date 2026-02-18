@echo off
setlocal

:: === AI-LogOps Agent Updater ===
:: 서비스 정지 → 백업 → 교체 → 시작 → 검증 → 실패 시 롤백

:: Step 1: 서비스 정지
net stop {service_name}
timeout /t 3 >nul

:: Step 2: 현재 폴더 백업 (config.yaml 포함 전체)
if exist "{backup_dir}" rmdir /S /Q "{backup_dir}"
mkdir "{backup_dir}"
xcopy "{install_dir}\*" "{backup_dir}\" /E /H /Y /Q >nul

:: Step 3: 새 파일 배치 (config.yaml 제외)
for /f "delims=" %%F in ('dir /b /a-d "{update_dir}"') do (
    if /I NOT "%%F"=="config.yaml" copy /Y "{update_dir}\%%F" "{install_dir}\%%F" >nul
)
:: 하위 디렉토리 복사 (config.yaml이 있는 루트만 보호)
for /d %%D in ("{update_dir}\*") do (
    xcopy "%%D" "{install_dir}\%%~nxD\" /E /H /Y /Q >nul
)

:: Step 4: 서비스 시작
net start {service_name}

:: Step 5: 시작 검증 (15초 대기 후 상태 확인)
timeout /t 15 >nul
sc query {service_name} | find "RUNNING" >nul
if errorlevel 1 (
    :: 실패: 롤백
    echo [ROLLBACK] Service failed to start. Restoring backup...
    xcopy "{backup_dir}\*" "{install_dir}\" /E /H /Y /Q >nul
    net start {service_name}
)

:: Step 6: 정리
if exist "{update_dir}" rmdir /S /Q "{update_dir}"

endlocal
