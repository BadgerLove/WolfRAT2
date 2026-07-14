@echo off
echo ============================================
echo  WolfRAT 2.1 — Build Script
echo ============================================
echo.

IF EXIST venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
) ELSE (
    echo Using system Python...
)

echo Installing/updating dependencies...
python -m pip install PyQt6 pyinstaller --quiet

echo.
echo Building WolfRAT 2.1 executable...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "WolfRAT2" ^
    --icon "wolfrat\icon.ico" ^
    --add-data "wolfrat;wolfrat" ^
    --add-data "..\..\..\fmj-updater\dist\updater.exe;." ^
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
