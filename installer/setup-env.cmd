@echo off
rem Run by the installer after it copies the files: installs Python and the pinned packages
rem into the install folder, so VoiceWriter never depends on a Python already on the PC.
setlocal
cd /d "%~dp0.."
set "UV_PYTHON_INSTALL_DIR=%CD%\python"
set "UV_CACHE_DIR=%CD%\cache"
set "UV_LINK_MODE=copy"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_NO_CONFIG=1"
set "UV_NO_PROGRESS=1"
echo ==== %DATE% %TIME% > install.log
tools\uv.exe venv .venv --python 3.13 --allow-existing >> install.log 2>&1 || exit /b 1
tools\uv.exe pip sync --python .venv\Scripts\python.exe --compile-bytecode requirements.txt >> install.log 2>&1 || exit /b 2
rem The packages are copied into .venv, so the download cache is only wasted space now.
tools\uv.exe cache clean >> install.log 2>&1
if exist cache rmdir /s /q cache
echo ==== done %TIME% >> install.log
exit /b 0
