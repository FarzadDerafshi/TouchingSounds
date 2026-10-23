@echo off
setlocal

:: =====================================================================
::  TouchingSounds production release (Office Server, D:\apps\TouchingSounds)
::  Double-click it. It backs up data\, pulls main from GitHub, rebuilds
::  the container and checks that the new build answers.
::  Full runbook: ProjectDocs\DEPLOYMENT.md
:: =====================================================================

:: --- Self-elevate to Administrator if not already running elevated
::     (same as the other projects' release scripts). ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrative privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: --- Run from a temporary copy of this script. cmd reads a running .bat
::     line by line from disk, so step 2's git pull replacing this very
::     file mid-run would make it execute garbled lines. The original hands
::     over to the copy (no CALL, so control never comes back) and passes
::     its own folder along. Step 2 stops if the pull changed this script,
::     so the new version runs from its first line on the next attempt. ---
if /i "%~1"=="--from-copy" goto :running_from_copy
copy /y "%~f0" "%TEMP%\touchingsounds_prodrelease.bat" >nul
"%TEMP%\touchingsounds_prodrelease.bat" --from-copy "%~dp0"
:running_from_copy
set "ROOT=%~2"
set "SELF_COPY=%~f0"
cd /d "%ROOT%"

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%T"
set "LOGFILE=%ROOT%deploy_log_%TS%.txt"
echo Log file: %LOGFILE%
echo Log file: %LOGFILE% > "%LOGFILE%"

echo ==========================================
echo Starting TouchingSounds Production Release...
echo ==========================================

:: --- Find git. It's often not on PATH, especially in the elevated window
::     (on 2026-10-19 the first run failed with "'git' is not recognized").
::     Look in the usual Git for Windows and GitHub Desktop locations and add
::     the first hit to PATH for this run. Checked before anything else
::     happens, so a missing git costs nothing. ---
where git >nul 2>&1
if %errorlevel% equ 0 goto :git_found
for %%G in ("%ProgramFiles%\Git\cmd" "%ProgramFiles(x86)%\Git\cmd" "%LOCALAPPDATA%\Programs\Git\cmd" "%ProgramW6432%\Git\cmd") do (
    if exist "%%~G\git.exe" set "PATH=%%~G;%PATH%"
)
where git >nul 2>&1
if %errorlevel% equ 0 goto :git_found
for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do (
    if exist "%%~D\resources\app\git\cmd\git.exe" set "PATH=%%~D\resources\app\git\cmd;%PATH%"
)
where git >nul 2>&1
if %errorlevel% equ 0 goto :git_found
echo [ERROR] git was not found on this machine.
echo Install Git for Windows, e.g. in PowerShell:  winget install --id Git.Git -e
echo then run this script again. Nothing has been changed.
echo git not found >> "%LOGFILE%"
pause
exit /b 1
:git_found
for /f "delims=" %%P in ('where git') do (
    echo Using %%P
    echo Using %%P >> "%LOGFILE%"
    goto :git_reported
)
:git_reported
:: The folder may be owned by another account than the elevated one; git
:: then refuses it as "dubious ownership". Trust this one folder only.
:: Passed as GIT_CONFIG_* environment variables rather than
:: "git -c safe.directory=...", because cmd splits that "=" inside
:: for /f ('...') and git then gets a mangled command line.
set "GIT_CONFIG_COUNT=1"
set "GIT_CONFIG_KEY_0=safe.directory"
set "GIT_CONFIG_VALUE_0=%ROOT:\=/%"
:: (ROOT ends in a backslash, so drop the trailing "/" it became)
set "GIT_CONFIG_VALUE_0=%GIT_CONFIG_VALUE_0:~0,-1%"

:: Step 1: Back up data\. It holds the speaker's original Sources.xml
:: (with the Spotify/Alexa logins; after the marge redirect it's the only
:: copy), favorites, history and presets. Gitignored, so git can't touch
:: it, but a copy per release costs nothing.
echo [1/5] Backing up data\ ...
if not exist "%ROOT%data" (
    echo data\ doesn't exist yet -- nothing to back up ^(first start^).
    goto :backup_done
)
if not exist "%ROOT%DataBackup" mkdir "%ROOT%DataBackup"
robocopy "%ROOT%data" "%ROOT%DataBackup\data_%TS%" /E /NFL /NDL /NJH /NJS /NP >nul
if %errorlevel% geq 8 (
    echo [ERROR] Backing up data\ failed! Aborting before touching any code or containers.
    pause
    exit /b 1
)
echo Backup saved: DataBackup\data_%TS%
echo Backup saved: DataBackup\data_%TS% >> "%LOGFILE%"
:backup_done

:: Step 2: Make the folder match origin/main exactly. The server holds no
:: code edits of its own, so this is a hard checkout rather than a merge
:: (a plain "git pull" refused on 2026-10-19, because the folder had been
:: updated by manual copies on top of an old checkout). Tracked files and
:: untracked files that main now tracks are overwritten; everything else
:: untracked (data\, DataBackup\, logs) is left alone. If tracked files
:: were edited on the server anyway, they're saved as a patch in
:: DataBackup\ first. A folder without .git (the pre-1.0.0 manual copy)
:: is turned into a checkout in place.
echo [2/5] Getting latest code from GitHub...
if exist "%ROOT%.git" goto :git_fetch
echo No .git folder -- converting this folder to a git checkout of main...
echo Converting folder to git checkout >> "%LOGFILE%"
git init -q >> "%LOGFILE%" 2>&1
git remote add origin https://github.com/FarzadDerafshi/TouchingSounds.git >> "%LOGFILE%" 2>&1

:git_fetch
git fetch origin main >> "%LOGFILE%" 2>&1
if %errorlevel% neq 0 goto :git_failed

:: Any edits to tracked files on the server? (no HEAD yet = fresh init)
git rev-parse -q --verify HEAD >nul 2>&1
if %errorlevel% neq 0 goto :git_checkout
set "LOCAL_EDITS="
for /f "delims=" %%L in ('git status --porcelain -uno') do set "LOCAL_EDITS=1"
if not defined LOCAL_EDITS goto :git_checkout
if not exist "%ROOT%DataBackup" mkdir "%ROOT%DataBackup"
git diff HEAD > "%ROOT%DataBackup\server_edits_%TS%.patch"
echo [WARNING] Files edited on the server were replaced by main. Their changes
echo           are saved in DataBackup\server_edits_%TS%.patch
echo Server edits saved to DataBackup\server_edits_%TS%.patch >> "%LOGFILE%"

:git_checkout
git checkout -f -B main origin/main >> "%LOGFILE%" 2>&1
if %errorlevel% neq 0 goto :git_failed
git branch -u origin/main main >> "%LOGFILE%" 2>&1
goto :git_done

:git_failed
type "%LOGFILE%"
echo [ERROR] Git failed! See log: %LOGFILE%
echo If it asked for a login: run "git fetch origin main" here once by hand
echo so Git Credential Manager stores it, then run this script again.
pause
exit /b 1

:git_done
type "%LOGFILE%"
fc /b "%SELF_COPY%" "%ROOT%ProdRelease.bat" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [STOP] This pull updated ProdRelease.bat itself. Nothing has been
    echo        rebuilt yet -- just run ProdRelease.bat again so the new
    echo        version runs from the start. ^(The data backup above is kept.^)
    echo ProdRelease.bat changed by pull -- stopped for re-run. >> "%LOGFILE%"
    pause
    exit /b 0
)

:: --- This server's settings (speaker IPs, BOSE_SERVER_URL, preset defaults)
::     live in .env, git-ignored, since the repo went public (2026-10-23).
::     docker-compose.yml refuses to start without it. If it's missing, take
::     the settings from the container that's running now, so the first
::     release after the switch needs no hands on the server. Written
::     without a BOM, which docker compose would read as part of the first key.
if exist "%ROOT%.env" goto :env_ok
echo No .env yet -- creating it from the running container's settings...
powershell -NoProfile -Command "$e = docker inspect touchingsounds --format '{{json .Config.Env}}' | ConvertFrom-Json; $k = @($e | Where-Object { $_ -match '^(BOSE_DEVICES|BOSE_SERVER_URL|BOSE_PORT|TS_SCAN_SUBNETS|PRESET_[1-6]_(URL|NAME))=' }); if (-not ($k -match '^BOSE_SERVER_URL=') -or -not ($k -match '^BOSE_DEVICES=')) { exit 1 }; [IO.File]::WriteAllLines('%ROOT%.env', [string[]]$k); Write-Host ('Wrote .env with ' + $k.Count + ' settings')"
if %errorlevel% neq 0 (
    echo [ERROR] There's no .env and the running container's settings couldn't be read.
    echo         Copy .env.example to .env in %ROOT%, fill it in, and run this again.
    echo         Nothing has been rebuilt.
    echo .env missing and not recoverable >> "%LOGFILE%"
    pause
    exit /b 1
)
echo .env created from the running container >> "%LOGFILE%"
:env_ok

:: Step 3: Rebuild and restart. Not redirected to the log: the first build
:: after an ffmpeg/requirements change takes a few minutes, and live output
:: shows the window isn't frozen. GIT_COMMIT is a build arg (see
:: docker-compose.yml) so /api/version and the app footer show the commit.
for /f %%C in ('git rev-parse --short HEAD') do set "GIT_COMMIT=%%C"
echo [3/5] Rebuilding and restarting touchingsounds at commit %GIT_COMMIT%...
echo Building commit %GIT_COMMIT% >> "%LOGFILE%"
docker compose up -d --build
if %errorlevel% neq 0 (
    echo [ERROR] Docker compose failed!
    echo Docker compose failed >> "%LOGFILE%"
    pause
    exit /b 1
)

:: Step 4: Confirm the new build answers. On start the app checks the
:: speaker's settings; if anything needed changing it reboots the speaker
:: and waits ~90 s before serving, so give it up to 4 minutes.
echo [4/5] Waiting for the new build to answer (up to 4 minutes)...
docker compose ps >> "%LOGFILE%" 2>&1
docker compose ps
powershell -NoProfile -Command "$log='%LOGFILE%'; for ($i = 0; $i -lt 80; $i++) { try { $v = Invoke-RestMethod 'http://localhost:8000/api/version' -TimeoutSec 3; $m = 'Running: v' + $v.version + ' (' + $v.commit + ')'; if ($v.commit -ne '%GIT_COMMIT%') { $m += ' [WARNING] expected commit %GIT_COMMIT%' }; Write-Host $m; Add-Content -Path $log -Value $m -Encoding ascii; exit 0 } catch { Start-Sleep -Seconds 3 } }; $m = '[WARNING] /api/version did not answer within 4 minutes -- check: docker compose logs touchingsounds'; Write-Host $m; Add-Content -Path $log -Value $m -Encoding ascii"

:: Step 5: Is the speaker reachable from the new container?
echo [5/5] Checking the speaker...
powershell -NoProfile -Command "$log='%LOGFILE%'; try { $s = Invoke-RestMethod 'http://localhost:8000/api/status' -TimeoutSec 10; $m = 'Speaker OK: source=' + $s.source + ' volume=' + $s.volume } catch { $m = '[WARNING] Speaker not reachable -- is it on, and reachable from this server?' }; Write-Host $m; Add-Content -Path $log -Value $m -Encoding ascii"

echo ==========================================
echo TouchingSounds Production Release Successful!
echo Log saved to: %LOGFILE%
echo Web app:  http://localhost:8000  ^(other devices: BOSE_SERVER_URL in .env^)
echo ==========================================
pause
endlocal
