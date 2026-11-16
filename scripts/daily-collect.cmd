@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
.venv\Scripts\python.exe -m workbench collect >> data\logs\daily-collect.log 2>&1
