@echo off
rem Personal quota dashboard startup entry: serves the dashboard on this PC.
cd /d "%~dp0"
python -m quota_dashboard %*
if errorlevel 1 pause
