@echo off
setlocal
cd /d "%~dp0"
echo Offer Compass - Alibaba Model Studio
echo The Base URL is shown in Model Studio and contains your WorkspaceId.
set /p OFFER_COMPASS_MODEL_BASE_URL=Alibaba OpenAI-compatible Base URL:
set /p OFFER_COMPASS_MODEL=Model ID [qwen3.8-flash]:
if "%OFFER_COMPASS_MODEL%"=="" set OFFER_COMPASS_MODEL=qwen3.8-flash
python -c "import sys; sys.exit(sys.version_info < (3, 7))" >nul 2>nul
if errorlevel 1 (
    py -3 "%~dp0offer_compass_server.py" --model-provider aliyun --ask-model-key --open
) else (
    python "%~dp0offer_compass_server.py" --model-provider aliyun --ask-model-key --open
)
if errorlevel 1 pause
