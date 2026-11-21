@echo off
rem Creates a Start Menu shortcut "Scan" for the current user, pointing to autoscan.pyw in this folder.
set "APP_DIR=%~dp0"
set "LNK_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Scan.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK_PATH); $s.TargetPath = Join-Path $env:APP_DIR 'autoscan.pyw'; $s.WorkingDirectory = $env:APP_DIR; $s.IconLocation = Join-Path $env:APP_DIR 'scanner.ico'; $s.Save()"

if errorlevel 1 (
    echo Could not create the shortcut.
) else (
    echo Shortcut created: %LNK_PATH%
)
pause
