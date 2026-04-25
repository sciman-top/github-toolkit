@echo off
if "%SystemRoot%"=="" set "SystemRoot=C:\Windows"
if "%WINDIR%"=="" set "WINDIR=%SystemRoot%"
if "%ComSpec%"=="" set "ComSpec=%SystemRoot%\System32\cmd.exe"
if "%PROGRAMDATA%"=="" set "PROGRAMDATA=C:\ProgramData"
if "%ProgramFiles%"=="" set "ProgramFiles=C:\Program Files"
if "%APPDATA%"=="" if not "%USERPROFILE%"=="" set "APPDATA=%USERPROFILE%\AppData\Roaming"
if "%LOCALAPPDATA%"=="" if not "%USERPROFILE%"=="" set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
if "%HTTP_PROXY%"=="" call :SetEnvFromRegistry HTTP_PROXY
if "%HTTPS_PROXY%"=="" call :SetEnvFromRegistry HTTPS_PROXY
if "%ALL_PROXY%"=="" call :SetEnvFromRegistry ALL_PROXY
if "%NO_PROXY%"=="" call :SetEnvFromRegistry NO_PROXY
echo ;%PATH%; | find /I ";%SystemRoot%\System32;" >nul || set "PATH=%SystemRoot%\System32;%PATH%"
echo ;%PATH%; | find /I ";%SystemRoot%;" >nul || set "PATH=%SystemRoot%;%PATH%"
echo ;%PATH%; | find /I ";%SystemRoot%\System32\WindowsPowerShell\v1.0;" >nul || set "PATH=%SystemRoot%\System32\WindowsPowerShell\v1.0;%PATH%"
if not "%ProgramFiles%"=="" if exist "%ProgramFiles%\GitHub CLI" echo ;%PATH%; | find /I ";%ProgramFiles%\GitHub CLI;" >nul || set "PATH=%ProgramFiles%\GitHub CLI;%PATH%"
goto :eof

:SetEnvFromRegistry
for /f "tokens=2,*" %%A in ('reg query HKCU\Environment /v %~1 2^>nul ^| findstr /I "%~1"') do set "%~1=%%B"
call if not "%%%~1%%"=="" goto :eof
for /f "tokens=2,*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v %~1 2^>nul ^| findstr /I "%~1"') do set "%~1=%%B"
goto :eof
