@echo off
rem Builds HomeDocsSetup.exe: a normal Windows installer people double-click.
rem Run from a Command Prompt in the homedocs folder:   packaging\build_windows.bat
rem Needs Python 3.10+ (python.org) and Inno Setup 6 (jrsoftware.org/isinfo.php).
setlocal
cd /d "%~dp0\.."

rem Python 3.12 if it's there (the model libraries may not support the newest Python yet), else "python"
set "PY=python"
py -3.12 -c "" >nul 2>nul && set "PY=py -3.12"

echo 1/4  Setting up a clean Python environment for building...
if not exist build\env\Scripts\python.exe %PY% -m venv build\env || goto fail
build\env\Scripts\python -m pip install --quiet --upgrade pip || goto fail
build\env\Scripts\python -m pip install --quiet -r requirements.txt pyinstaller || goto fail

echo 2/4  Bundling HomeDocs with PyInstaller...
if exist build\dist rmdir /s /q build\dist
build\env\Scripts\pyinstaller --noconfirm --clean --log-level ERROR --distpath build\dist --workpath build\work packaging\homedocs.spec || goto fail
rem ONNX Runtime needs Microsoft's C++ runtime, which not every PC has (GitHub's build machines always do, so
rem the self-test can't catch it): the app must carry it
for %%D in (msvcp140.dll msvcp140_1.dll vcruntime140.dll vcruntime140_1.dll) do (
  dir /s /b build\dist\HomeDocs\%%D >nul 2>nul || (echo %%D isn't in the bundle: ONNX Runtime won't start on PCs without it. & goto fail)
)

echo 3/4  Checking that the bundled app starts...
rem (with a data folder of its own, so it never touches yours; models put in build\selftest\models
rem are checked too)
if exist build\selftest\app.log del build\selftest\app.log
set "HOMEDOCS_HOME=%CD%\build\selftest"
start /wait "" build\dist\HomeDocs\HomeDocs.exe --self-test
if errorlevel 1 (
  set "HOMEDOCS_HOME="
  type build\selftest\app.log
  echo The self-test failed: see above.
  goto fail
)
set "HOMEDOCS_HOME="
type build\selftest\app.log

echo 4/4  Making the installer...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 isn't installed, so no installer was made.
  echo The app itself is ready in build\dist\HomeDocs\ ^(HomeDocs.exe^).
  echo Install Inno Setup from https://jrsoftware.org/isinfo.php and run this again for the installer.
  exit /b 0
)
"%ISCC%" /Q packaging\homedocs.iss || goto fail

echo.
echo Done: HomeDocsSetup.exe
exit /b 0

:fail
echo.
echo The build stopped because of the error above.
exit /b 1
