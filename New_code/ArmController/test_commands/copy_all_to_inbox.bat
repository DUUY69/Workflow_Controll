@echo off
setlocal
set INBOX=..\inbox
if not exist "%INBOX%" mkdir "%INBOX%"

copy /Y ".\upload_move_lua.json" "%INBOX%" >nul
copy /Y ".\run_move_lua.json" "%INBOX%" >nul
copy /Y ".\upload_db_activate.json" "%INBOX%" >nul
copy /Y ".\upload_db_no_activate.json" "%INBOX%" >nul

echo Copied test JSONs to %INBOX%
endlocal

