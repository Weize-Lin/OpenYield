@echo off
powershell.exe -NoProfile -File "%~dp0stop.ps1"
if errorlevel 1 pause
