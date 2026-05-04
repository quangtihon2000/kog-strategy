@echo off
REM Double-click wrapper for check-agents.ps1 — keeps window open after run.
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0check-agents.ps1" %*
