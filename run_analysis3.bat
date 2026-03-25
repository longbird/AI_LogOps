@echo off
cd /d D:\Work\AI_Projects\AI-LogOps
"C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3312.0_x64__qbz5n2kfra8p0\python3.13.exe" run_analysis2.py > run_analysis_log.txt 2>&1
echo EXITCODE=%ERRORLEVEL% >> run_analysis_log.txt
