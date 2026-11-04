@echo off
rem Builds llama.cpp (the release pinned in build_llama.sh) and native\hd_llama.c into build\llama\ (or the folder
rem given): the libraries core\llm.py loads to write answers. The Windows twin of build_llama.sh.
rem
rem   packaging\build_llama.bat [OUT_DIR]
rem
rem Needs git and Visual Studio 2022 or newer with "Desktop development with C++" (it brings CMake and Ninja).
rem With LLVM's clang installed too (llvm.org, or Visual Studio's "C++ Clang tools") it builds with clang, as
rem llama.cpp's own Windows release does: its CPU code runs faster built that way. Otherwise it uses MSVC.
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

rem the pin lives in build_llama.sh only, so the two can't drift apart
for /f "tokens=2 delims==# " %%V in ('findstr /b "LLAMA_TAG=" packaging\build_llama.sh') do set "LLAMA_TAG=%%V"
for /f "tokens=2 delims==# " %%V in ('findstr /b "LLAMA_COMMIT=" packaging\build_llama.sh') do set "LLAMA_COMMIT=%%V"
set "OUT=%~1"
if "%OUT%"=="" set "OUT=%CD%\build\llama"
set "SRC=%CD%\build\llama-src"
set "WORK=%CD%\build\llama-build"

if not exist "%SRC%\.git" (
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch %LLAMA_TAG% https://github.com/ggml-org/llama.cpp "%SRC%" || goto fail
)
for /f %%C in ('git -C "%SRC%" rev-parse HEAD') do set "GOT=%%C"
if not "%GOT%"=="%LLAMA_COMMIT%" (
  echo build\llama-src is at %GOT%, not %LLAMA_TAG% ^(%LLAMA_COMMIT%^). Delete it and run this again.
  goto fail
)

rem Visual Studio's build environment (compiler, linker, Windows SDK, CMake, Ninja)
if not defined VCINSTALLDIR (
  set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
  if not exist "!VSWHERE!" (echo Visual Studio with C++ isn't installed. & goto fail)
  for /f "usebackq delims=" %%I in (`"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS=%%I"
  if not defined VS (echo Visual Studio has no C++ tools: add "Desktop development with C++". & goto fail)
  call "!VS!\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul || goto fail
)

set "COMPILER=-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++"
where clang >nul 2>nul || (
  if exist "%VCINSTALLDIR%Tools\Llvm\x64\bin\clang.exe" (
    set "PATH=%VCINSTALLDIR%Tools\Llvm\x64\bin;!PATH!"
  ) else (
    echo clang isn't installed: building with MSVC ^(answers will be written a little slower^).
    set "COMPILER=-DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl"
  )
)

rem the same settings as build_llama.sh (one CPU backend per feature level, picked at run time; no GPU code)
cmake -S native -B "%WORK%" -G Ninja %COMPILER% -DLLAMA_DIR="%SRC%" -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_OPENMP=OFF ^
  -DGGML_METAL=OFF -DGGML_BLAS=OFF -DGGML_VULKAN=OFF -DGGML_CUDA=OFF ^
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_COMMON=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF ^
  -DLLAMA_BUILD_TOOLS=OFF -DLLAMA_BUILD_SERVER=OFF >"%WORK%.log" 2>&1 || (type "%WORK%.log" & goto fail)
cmake --build "%WORK%" --config Release >"%WORK%.log" 2>&1 || (type "%WORK%.log" & goto fail)

if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%" || goto fail
rem every DLL it made: llama, ggml, ggml-base, the CPU backends (loaded by path at run time) and hd_llama
rem (ggml puts some in bin\, the rest go in out\)
for %%D in (out bin) do if exist "%WORK%\%%D\*.dll" copy /y "%WORK%\%%D\*.dll" "%OUT%\" >nul
if not exist "%OUT%\hd_llama.dll" (echo hd_llama.dll wasn't built. & goto fail)
echo Built llama.cpp %LLAMA_TAG% with hd_llama into %OUT%:
dir /b "%OUT%"
exit /b 0

:fail
echo.
echo Building llama.cpp stopped because of the error above.
exit /b 1
