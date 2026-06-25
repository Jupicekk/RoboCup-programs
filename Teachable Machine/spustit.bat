@echo off
chcp 65001 >nul
echo ====================================================
echo   Detektor vystrelu katapultu - spustam...
echo   (okno zatvori klaves 'q' v okne s obrazom)
echo ====================================================
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%~dp0detektor_katapult.py"
echo.
echo Program skoncil. Stlac lubovolny klaves pre zatvorenie.
pause >nul
