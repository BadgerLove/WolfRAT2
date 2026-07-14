@echo off
echo ============================================
echo  WolfRAT 2.0 — Build Script
echo ============================================
echo.

REM Activate venv
call venv\Scripts\activate.bat

echo Installing/updating dependencies...
pip install PyQt6 pyinstaller --quiet

echo.
echo Building WolfRAT 2.0 executable...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "WolfRAT2" ^
    --add-data "wolfrat;wolfrat" ^
    --clean ^
    --noconfirm ^
    main.py

echo.
if exist dist\WolfRAT2.exe (
    echo ============================================
    echo  BUILD SUCCESSFUL!
    echo  Executable: dist\WolfRAT2.exe
    echo ============================================
) else (
    echo ============================================
    echo  BUILD FAILED — check output above
    echo ============================================
)
pause
