@echo off
setlocal DisableDelayedExpansion
if not exist "app.py" goto :wrong_folder
if not exist "requirements.txt" goto :wrong_folder
if "%~1"=="8501" goto :setup
if "%~1"=="8502" goto :setup
goto :wrong_folder

:setup
set "venv_python=.venv\Scripts\python.exe"
if exist "%venv_python%" goto :verify
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto :create_with_py
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 goto :no_python
python -m venv ".venv"
if errorlevel 1 goto :venv_error
goto :verify

:create_with_py
py -3 -m venv ".venv"
if errorlevel 1 goto :venv_error

:verify
"%venv_python%" -c "import sys; sys.exit(sys.version_info < (3, 11) or sys.prefix == sys.base_prefix)"
if errorlevel 1 goto :venv_error
"%venv_python%" -m pip --disable-pip-version-check --no-input install -r "requirements.txt"
if errorlevel 1 goto :install_error
"%venv_python%" -m streamlit run "app.py" --server.address=127.0.0.1 --server.port=%~1 --server.headless=false --server.showEmailPrompt=false --browser.serverAddress=127.0.0.1 --browser.gatherUsageStats=false
exit /b %errorlevel%

:wrong_folder
echo Start the application using music\run.bat or video\run.bat.
exit /b 1

:no_python
echo Install Python 3.11 or newer for Windows with the Python launcher or PATH enabled.
exit /b 1

:venv_error
echo Could not use the local .venv with Python 3.11 or newer.
echo Check Python and folder permissions. If .venv is outdated, rename it and try again.
exit /b 1

:install_error
echo Dependency installation failed. See the pip message above and retry run.bat.
exit /b 1
