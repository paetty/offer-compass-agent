@echo off
setlocal
cd /d "%~dp0"
echo Offer Compass - Alibaba Model Studio
echo The first launch stores the configuration encrypted for your Windows account.
if not exist "%~dp0data\model-credentials.bin" (
    python "%~dp0offer_compass_server.py" --model-provider aliyun --model-name qwen3.8-flash --use-saved-model-config --ask-model-key --remember-model-config --configure-model-only
    if errorlevel 1 goto failed
)
powershell -NoProfile -Command "Start-Process -FilePath 'python' -ArgumentList '%~dp0offer_compass_server.py','--use-saved-model-config' -WorkingDirectory '%~dp0' -WindowStyle Hidden"
goto started

:started
echo Offer Compass is running at http://127.0.0.1:8765/
timeout /t 2 /nobreak >nul
exit /b 0

:failed
echo Model configuration failed. The credential was not saved.
pause
exit /b 1
