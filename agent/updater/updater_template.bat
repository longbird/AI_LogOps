@echo off
setlocal

:: Step 1: 서비스 정지
net stop {service_name}

:: Step 2: 현재 파일 백업 (절대 삭제 안 함)
copy /Y "{current_exe}" "{backup_path}"

:: Step 3: 새 파일 배치
copy /Y "{new_exe}" "{current_exe}"

:: Step 4: 서비스 시작
net start {service_name}

:: Step 5: 시작 검증 (10초 대기 후 상태 확인)
timeout /t 10 >nul
sc query {service_name} | find "RUNNING" >nul
if errorlevel 1 (
    :: 실패: 롤백
    copy /Y "{backup_path}" "{current_exe}"
    net start {service_name}
)

endlocal
