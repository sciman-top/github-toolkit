param(
    [switch]$PassThru,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

function Set-DefaultEnvVar {
    param(
        [string]$Name,
        [string]$Value,
        [System.Collections.Generic.List[string]]$Repaired
    )

    if ([string]::IsNullOrWhiteSpace($Value)) { return }
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
        [void]$Repaired.Add($Name)
    }
}

function Read-CodexShellEnvironmentPolicySet {
    $codexHome = [Environment]::GetEnvironmentVariable("CODEX_HOME", "Process")
    if ([string]::IsNullOrWhiteSpace($codexHome)) {
        $userProfile = [Environment]::GetEnvironmentVariable("USERPROFILE", "Process")
        if ([string]::IsNullOrWhiteSpace($userProfile)) {
            $userProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
        }
        if ([string]::IsNullOrWhiteSpace($userProfile)) {
            return @{}
        }
        $codexHome = Join-Path $userProfile ".codex"
    }

    $configPath = Join-Path $codexHome "config.toml"
    if (-not (Test-Path -LiteralPath $configPath)) {
        return @{}
    }

    $values = @{}
    $insidePolicySet = $false
    foreach ($rawLine in Get-Content -LiteralPath $configPath -ErrorAction SilentlyContinue) {
        $line = [string]$rawLine
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -match '^\[(.+)\]$') {
            $insidePolicySet = ($matches[1] -eq "shell_environment_policy.set")
            continue
        }
        if (-not $insidePolicySet) {
            continue
        }
        if ($trimmed -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"(.*)"\s*$') {
            $name = $matches[1]
            $value = $matches[2].Replace('\"', '"').Replace('\\', '\')
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $values[$name] = $value
            }
        }
    }
    return $values
}

function Import-SafeCodexShellEnvironmentPolicy {
    param([System.Collections.Generic.List[string]]$Repaired)

    $safeNames = @(
        "COMSPEC", "ComSpec",
        "WINDIR", "windir",
        "SYSTEMROOT", "SystemRoot",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "ProgramFiles",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy"
    )

    $policySet = Read-CodexShellEnvironmentPolicySet
    foreach ($name in $safeNames) {
        if ($policySet.ContainsKey($name)) {
            Set-DefaultEnvVar -Name $name -Value ([string]$policySet[$name]) -Repaired $Repaired
            continue
        }
        $userValue = [Environment]::GetEnvironmentVariable($name, "User")
        if (-not [string]::IsNullOrWhiteSpace($userValue)) {
            Set-DefaultEnvVar -Name $name -Value $userValue -Repaired $Repaired
            continue
        }
        $machineValue = [Environment]::GetEnvironmentVariable($name, "Machine")
        if (-not [string]::IsNullOrWhiteSpace($machineValue)) {
            Set-DefaultEnvVar -Name $name -Value $machineValue -Repaired $Repaired
        }
    }
}

function Add-PathPrefix {
    param(
        [string]$PathEntry,
        [System.Collections.Generic.List[string]]$Repaired
    )

    if ([string]::IsNullOrWhiteSpace($PathEntry)) { return }
    if (-not (Test-Path -LiteralPath $PathEntry)) { return }

    $currentPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
    $parts = @($currentPath -split [IO.Path]::PathSeparator | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $exists = @($parts | Where-Object { $_.TrimEnd("\") -ieq $PathEntry.TrimEnd("\") }).Count -gt 0
    if ($exists) { return }

    [Environment]::SetEnvironmentVariable("PATH", ($PathEntry + [IO.Path]::PathSeparator + $currentPath), "Process")
    [void]$Repaired.Add("PATH")
}

$repaired = [System.Collections.Generic.List[string]]::new()
Import-SafeCodexShellEnvironmentPolicy -Repaired $repaired
$systemRoot = $env:SystemRoot
if ([string]::IsNullOrWhiteSpace($systemRoot)) { $systemRoot = $env:WINDIR }
if ([string]::IsNullOrWhiteSpace($systemRoot) -and -not [string]::IsNullOrWhiteSpace($env:ComSpec)) {
    $systemRoot = Split-Path -Parent (Split-Path -Parent $env:ComSpec)
}
if ([string]::IsNullOrWhiteSpace($systemRoot)) { $systemRoot = "C:\Windows" }
if ((Split-Path -Leaf $systemRoot) -ieq "System32") {
    $systemRoot = Split-Path -Parent $systemRoot
}

$userProfile = $env:USERPROFILE
if ([string]::IsNullOrWhiteSpace($userProfile) -and -not [string]::IsNullOrWhiteSpace($env:HOMEDRIVE) -and -not [string]::IsNullOrWhiteSpace($env:HOMEPATH)) {
    $userProfile = "$env:HOMEDRIVE$env:HOMEPATH"
}

Set-DefaultEnvVar -Name "SystemRoot" -Value $systemRoot -Repaired $repaired
Set-DefaultEnvVar -Name "WINDIR" -Value $systemRoot -Repaired $repaired
Set-DefaultEnvVar -Name "ComSpec" -Value (Join-Path $systemRoot "System32\cmd.exe") -Repaired $repaired
Set-DefaultEnvVar -Name "PROGRAMDATA" -Value "C:\ProgramData" -Repaired $repaired
Set-DefaultEnvVar -Name "ProgramFiles" -Value "C:\Program Files" -Repaired $repaired

if (-not [string]::IsNullOrWhiteSpace($userProfile)) {
    Set-DefaultEnvVar -Name "APPDATA" -Value (Join-Path $userProfile "AppData\Roaming") -Repaired $repaired
    Set-DefaultEnvVar -Name "LOCALAPPDATA" -Value (Join-Path $userProfile "AppData\Local") -Repaired $repaired
}

Add-PathPrefix -PathEntry (Join-Path $systemRoot "System32") -Repaired $repaired
Add-PathPrefix -PathEntry $systemRoot -Repaired $repaired
if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles})) {
    Add-PathPrefix -PathEntry (Join-Path ${env:ProgramFiles} "PowerShell\7") -Repaired $repaired
    Add-PathPrefix -PathEntry (Join-Path ${env:ProgramFiles} "GitHub CLI") -Repaired $repaired
}
Add-PathPrefix -PathEntry (Join-Path $systemRoot "System32\WindowsPowerShell\v1.0") -Repaired $repaired

if (-not $Quiet -and $repaired.Count -gt 0) {
    Write-Host ("[INFO] Windows process environment repaired: {0}" -f (($repaired | Sort-Object -Unique) -join ", "))
}

if ($PassThru) {
    [pscustomobject]@{
        Repaired = @($repaired | Sort-Object -Unique)
        SystemRoot = $env:SystemRoot
        APPDATA = $env:APPDATA
        LOCALAPPDATA = $env:LOCALAPPDATA
        ComSpec = $env:ComSpec
    }
}
