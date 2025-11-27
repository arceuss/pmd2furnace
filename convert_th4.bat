@echo off
echo Converting all PMD files from TH4 - LLS folder...
echo.

for %%f in ("TH4 - LLS\*.M") do (
    echo Converting: %%~nxf
    python pmd2furnace.py "%%f"
    echo.
)

echo.
echo Done! All files converted.
pause

