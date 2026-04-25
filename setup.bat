@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

call "%~dp0scripts\Initialize-WindowsProcessEnvironment.cmd"

echo ============================================
echo   GitHub Toolkit - Setup
echo ============================================
echo.

set "AUTH_MODE=none"
set "TOKEN_SOURCE="

:: --- 1. Check / Install gh CLI ---
where gh >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [1/3] gh CLI not found. Installing...
    winget install --id GitHub.cli --source winget --accept-source-agreements --accept-package-agreements
    if %ERRORLEVEL% neq 0 (
        echo.
        echo ERROR: winget install failed.
        echo Please download manually from: https://github.com/cli/cli/releases/latest
        echo Download gh_*_windows_amd64.msi, install, then re-run this script.
        pause
        exit /b 1
    )
    echo.
    echo gh CLI installed. Refreshing PATH...
    set "PATH=%PATH%;C:\Program Files\GitHub CLI"
    where gh >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo.
        echo WARNING: gh still not in PATH. Please close this window,
        echo open a NEW terminal, then re-run this script.
        pause
        exit /b 1
    )
) else (
    echo [1/3] gh CLI: found
)

:: --- 2. Check / Do Authentication ---
:: Supported modes:
::   1) GH_TOKEN / GITHUB_TOKEN in environment ^(preferred for automation^)
::   2) gh auth login local configuration ^(compatibility fallback^)
if not "%GH_TOKEN%"=="" (
    set "AUTH_MODE=env"
    set "TOKEN_SOURCE=GH_TOKEN"
) else if not "%GITHUB_TOKEN%"=="" (
    set "AUTH_MODE=env"
    set "TOKEN_SOURCE=GITHUB_TOKEN"
)

if "%AUTH_MODE%"=="env" (
    echo [2/3] Authentication: using environment token ^(!TOKEN_SOURCE!^)
    gh api user >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo ERROR: The token from !TOKEN_SOURCE! is invalid or missing GitHub API access.
        pause
        exit /b 1
    )
) else (
    :: NOTE: gh auth login stores an HTTPS token in local config.
    gh auth status >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo.
        echo [2/3] Not authenticated. Starting login...
        echo   * Using HTTPS protocol with local gh credential storage
        echo.
        gh auth login -h github.com -p https -w
        if %ERRORLEVEL% neq 0 (
            echo ERROR: Login failed.
            pause
            exit /b 1
        )
    ) else (
        echo [2/3] Authentication: OK ^(gh auth login^)
    )
    set "AUTH_MODE=gh_login"
)

:: --- 3. Check / Add workflow scope ---
if "%AUTH_MODE%"=="env" (
    echo [3/3] Workflow scope: managed by !TOKEN_SOURCE!
    echo         Ensure the provided token has repo/workflow permissions before modifying workflows.
) else (
    gh auth status 2>&1 | findstr /C:"workflow" >nul
    if %ERRORLEVEL% neq 0 (
        echo.
        echo [3/3] Adding 'workflow' scope ^(required to manage workflow files^)...
        gh auth refresh -s workflow
        if %ERRORLEVEL% neq 0 (
            echo ERROR: Failed to add workflow scope.
            pause
            exit /b 1
        )
    ) else (
        echo [3/3] Workflow scope: OK
    )
)

echo.
echo ============================================
echo   Setup complete!
echo ============================================
echo.
echo Usage:
echo   python sync-forks.py --add-workflow     Add sync workflow to all forks
echo   python sync-forks.py                    Sync all forks now
echo   python sync-forks.py --dry-run          Preview without changes
echo   python sync-forks.py --json             Output machine-readable summary
echo   python list-forks.py --json             Output fork status as JSON
echo.
pause
