@echo off
rem gamecore-addon on PATH: the Python CLI next to this file, on the installation's venv.
setlocal
set "PYTHONUTF8=1"
"%~dp0..\..\.venv\Scripts\python.exe" "%~dp0gamecore-addon" %*
