@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || goto :directory_error
call "..\launch.cmd" 8501
set "result=%errorlevel%"
popd
if not "%result%"=="0" pause
exit /b %result%

:directory_error
echo Could not open the application folder.
pause
exit /b 1
