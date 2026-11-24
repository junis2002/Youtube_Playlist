@echo off
setlocal DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
call "launch.cmd" 8503
set "result=%errorlevel%"
popd
if not "%result%"=="0" pause
exit /b %result%
