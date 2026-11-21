@echo off
REM Local build on a Windows PC: needs Python 3.11 and ffmpeg.exe + ffprobe.exe in the "ffmpeg" folder next to this file.
python -m pip install -r requirements.txt pyinstaller || goto :err
pyinstaller --noconfirm --windowed --name RecapMM --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all av --collect-all edge_tts --collect-all certifi --collect-all tokenizers --collect-all huggingface_hub --exclude-module tkinter --add-data "recapmm/assets;recapmm/assets" run.py || goto :err
copy /Y ffmpeg\ffmpeg.exe dist\RecapMM\ >nul
copy /Y ffmpeg\ffprobe.exe dist\RecapMM\ >nul
echo.
echo Done: dist\RecapMM\RecapMM.exe
exit /b 0
:err
echo Build failed
exit /b 1
