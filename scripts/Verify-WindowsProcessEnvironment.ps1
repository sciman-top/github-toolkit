param(
    [switch]$Json,
    [switch]$SkipGh
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "Initialize-WindowsProcessEnvironment.ps1") -Quiet

function New-Check {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Message
    )

    [pscustomobject]@{
        name = $Name
        ok = $Ok
        message = $Message
    }
}

$checks = [System.Collections.Generic.List[object]]::new()
$required = @("SystemRoot", "WINDIR", "ComSpec", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ProgramFiles")
foreach ($name in $required) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    [void]$checks.Add((New-Check -Name $name -Ok (-not [string]::IsNullOrWhiteSpace($value)) -Message $value))
}

foreach ($pathName in @("SystemRoot", "APPDATA", "LOCALAPPDATA")) {
    $value = [Environment]::GetEnvironmentVariable($pathName, "Process")
    [void]$checks.Add((New-Check -Name "$pathName path" -Ok ((-not [string]::IsNullOrWhiteSpace($value)) -and (Test-Path -LiteralPath $value)) -Message $value))
}

$gh = Get-Command gh -ErrorAction SilentlyContinue
[void]$checks.Add((New-Check -Name "gh command" -Ok ($null -ne $gh) -Message ($(if ($gh) { $gh.Source } else { "not found" }))))

if (-not $SkipGh -and $gh) {
    $authOutput = (& gh auth status 2>&1) | Out-String
    [void]$checks.Add((New-Check -Name "gh auth status" -Ok ($LASTEXITCODE -eq 0) -Message $authOutput.Trim()))
}

$python = Get-Command python -ErrorAction SilentlyContinue
[void]$checks.Add((New-Check -Name "python command" -Ok ($null -ne $python) -Message ($(if ($python) { $python.Source } else { "not found" }))))

if ($python) {
    $dnsOutput = (& python -c "import socket; print(socket.getaddrinfo('api.github.com', 443)[0][4][0])" 2>&1) | Out-String
    [void]$checks.Add((New-Check -Name "python dns" -Ok ($LASTEXITCODE -eq 0) -Message $dnsOutput.Trim()))
}

$ok = @($checks | Where-Object { -not $_.ok }).Count -eq 0
$result = [pscustomobject]@{
    ok = $ok
    checked_at_utc = [DateTime]::UtcNow.ToString("o")
    checks = @($checks)
}

if ($Json) {
    $result | ConvertTo-Json -Depth 5
}
else {
    foreach ($check in $checks) {
        $level = if ($check.ok) { "PASS" } else { "FAIL" }
        Write-Host ("[{0}] {1}: {2}" -f $level, $check.name, $check.message)
    }
}

if ($ok) { exit 0 }
exit 1
