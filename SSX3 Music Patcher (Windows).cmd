@echo off
rem ===========================================================
rem  SSX 3 Custom Music Patcher  --  WINDOWS
rem  Double-click this file. Mac users: use the .command file.
rem ===========================================================
cd /d "%~dp0"
start "" pythonw "%~dp0ssx3_music_gui.py"
if errorlevel 1 (
  echo Could not start with pythonw, trying py...
  py "%~dp0ssx3_music_gui.py"
  pause
)
