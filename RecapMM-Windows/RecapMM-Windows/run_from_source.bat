@echo off
REM Run without building an exe: Python 3.10+ and ffmpeg on PATH (or ffmpeg.exe next to this file).
python -m pip install -r requirements.txt && python run.py
