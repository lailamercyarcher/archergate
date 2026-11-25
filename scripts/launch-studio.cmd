@echo off
setlocal
pushd "%~dp0.." || exit /b 1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m archergate.studio launch
) else (
  py -3.14 -m archergate.studio launch
)
set "status=%ERRORLEVEL%"
if not "%status%"=="0" pause
popd
exit /b %status%
