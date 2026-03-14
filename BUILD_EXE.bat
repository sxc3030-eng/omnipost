@echo off
title OmniPost — Build .exe
cd /d "%~dp0"
echo.
echo  ===================================
echo   OmniPost — Build Windows .exe
echo  ===================================
echo.

REM Install dependencies
echo [1/4] Installation des dependances...
pip install pyinstaller websockets --quiet

REM Create spec file
echo [2/4] Creation du fichier spec...

echo [3/4] Build OmniPost...
pyinstaller --onefile --noconsole ^
  --name "OmniPost" ^
  --icon=icon.ico ^
  --add-data "omnipost_dashboard.html;." ^
  --add-data "competitor_analyzer.html;." ^
  --hidden-import websockets ^
  --hidden-import websockets.legacy ^
  --hidden-import websockets.legacy.server ^
  --hidden-import asyncio ^
  omnipost.py

echo [4/4] Build Competitor Analyzer...
pyinstaller --onefile --noconsole ^
  --name "CompetitorAnalyzer" ^
  --icon=icon.ico ^
  --add-data "competitor_analyzer.html;." ^
  --hidden-import websockets ^
  --hidden-import websockets.legacy ^
  --hidden-import websockets.legacy.server ^
  --hidden-import asyncio ^
  competitor_analyzer.py

echo.
echo  ===================================
echo   Build termine !
echo   Fichiers dans le dossier dist/
echo  ===================================
echo.
echo  OmniPost.exe
echo  CompetitorAnalyzer.exe
echo.
pause
