@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0data\model-credentials.bin" del /f /q "%~dp0data\model-credentials.bin"
call "%~dp0启动 Offer Compass - 阿里云模型.cmd"
