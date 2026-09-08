@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "mode=%~1"
set "exit_code=0"
if not "%~2"=="" goto usage_error
if /I "%mode%"=="--help" goto help
if /I "%mode%"=="--check" goto prepare
if /I "%mode%"=="--build-only" goto prepare
if not "%mode%"=="" goto usage_error

:prepare
pushd "%~dp0" >nul
if errorlevel 1 goto directory_error
set "EXAM_GURU_MAX_UPLOAD_BYTES=268435456"
set "EXAM_GURU_OCR_TESSERACT_MAX_SOURCE_BYTES=268435456"
set "EXAM_GURU_OCR_TESSERACT_MAX_PAGES=40"
set "EXAM_GURU_OCR_TESSERACT_TIMEOUT_SECONDS=5"

docker --context desktop-linux info >nul 2>&1
if errorlevel 1 goto docker_error
docker --context desktop-linux compose --project-name ai-exam-guru --file "%~dp0compose.yaml" config --quiet
if errorlevel 1 goto failed
if /I "%mode%"=="--check" goto checked
if /I "%mode%"=="--build-only" goto build_only

docker --context desktop-linux volume inspect ai-exam-guru_postgres18-data >nul 2>&1
if not errorlevel 1 goto run
echo No existing Docker Desktop Studio database volume was found.
echo This will create a NEW EMPTY database. Existing PDFs will be kept.
echo PDF files are not automatically imported or restored.
choice /C YN /N /M "Start a fresh local Studio? [Y/N]: "
if errorlevel 2 goto cancelled
if not errorlevel 1 goto cancelled

:run
echo Building and starting the ai-exam-guru Compose group...
echo Pending database migrations will run. Back up important data before upgrades.
docker --context desktop-linux compose --project-name ai-exam-guru --file "%~dp0compose.yaml" up --build --detach --wait --wait-timeout 900
if errorlevel 1 goto failed
echo.
echo Studio is running. Open http://localhost:3000 with the default port settings.
echo Manage the whole ai-exam-guru group from Docker Desktop, Containers.
echo The migrate service exiting with code 0 is normal.
goto done

:build_only
docker --context desktop-linux compose --project-name ai-exam-guru --file "%~dp0compose.yaml" build
if errorlevel 1 goto failed
echo Images built. No Studio containers were started.
goto done

:checked
echo Configuration valid. No Studio containers were started.
goto done

:cancelled
echo Cancelled. No Studio containers were started.
goto done

:docker_error
echo Docker Desktop is unavailable. Open Docker Desktop with Linux containers and try again.
goto failed

:failed
echo The command failed. Check the error above and your local configuration.
echo This launcher does not delete database volumes or source files.
set "exit_code=1"

:done
popd
if "%mode%"=="" pause
exit /b %exit_code%

:directory_error
echo Cannot open the project directory.
if "%mode%"=="" pause
exit /b 1

:usage_error
echo Unknown launcher option. Use --help for supported options.
exit /b 2

:help
echo Double-click start-studio.cmd to build and run the entire local Studio.
echo   --check       Validate configuration without starting services.
echo   --build-only  Build images without starting services.
echo   --help        Show this help.
echo Uses Docker Desktop, compose.yaml and the existing local env configuration.
echo Applies the local legacy OCR profile: 256 MiB, 40 pages, 5 seconds.
echo Database volumes and original PDFs are never removed by this launcher.
exit /b 0
