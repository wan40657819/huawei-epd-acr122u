@echo off
cd /d "%~dp0"
python epd_gui.py %*
if errorlevel 1 pause
