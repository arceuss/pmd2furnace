@echo off
setlocal enabledelayedexpansion

echo ============================================
echo PMD to Furnace Batch Converter
echo ============================================
echo.

set "INPUT_DIR=PC-98 Audio Rips"
set "OUTPUT_DIR=Furnace Output"

:: Create main output directory
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

:: Process each game folder
for /d %%G in ("%INPUT_DIR%\*") do (
    set "GAME_NAME=%%~nxG"
    echo.
    echo Processing: !GAME_NAME!
    echo ----------------------------------------
    
    :: Create output folder for this game
    if not exist "%OUTPUT_DIR%\!GAME_NAME!" mkdir "%OUTPUT_DIR%\!GAME_NAME!"
    
    :: Convert each .M file in the game folder
    for %%F in ("%%G\*.M") do (
        set "FILENAME=%%~nF"
        echo   Converting: %%~nxF
        python pmd2furnace.py "%%F" "%OUTPUT_DIR%\!GAME_NAME!\!FILENAME!.fur"
    )
)

echo.
echo ============================================
echo Conversion complete!
echo Output saved to: %OUTPUT_DIR%
echo ============================================
pause

