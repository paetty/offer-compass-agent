@echo off
if exist "%~dp0data\model-credentials.bin" del /f /q "%~dp0data\model-credentials.bin"
call "%~dp0start_aliyun_model.cmd"
