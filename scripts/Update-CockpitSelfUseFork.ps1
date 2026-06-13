param(
    [string]$RepoPath = "D:\CODE\external\Cockpit-Tools-Local",
    [string]$SelfUseBranch = "main",
    [string]$ForkRemote = "origin",
    [string]$UpstreamRemote = "upstream",
    [string]$UpstreamBranch = "main",
    [string]$ReviewBranch = "",
    [string]$WorktreePath = "",
    [switch]$PlanOnly,
    [switch]$InstallDeps,
    [switch]$RunBuild,
    [switch]$Push
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    param(
        [string]$Cwd,
        [string[]]$GitArgs
    )
    & git -C $Cwd @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git -C $Cwd $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-CommandChecked {
    param(
        [string]$WorkingDirectory,
        [string]$FilePath,
        [string[]]$Arguments
    )
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$FilePath $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

function Get-GitOutput {
    param(
        [string]$Cwd,
        [string[]]$GitArgs
    )
    $output = & git -C $Cwd @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git -C $Cwd $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
    return ($output -join "`n").Trim()
}

function ConvertTo-BranchSlug {
    param([string]$Value)
    $slug = $Value -replace "^v", ""
    $slug = $slug -replace "[^0-9A-Za-z._-]", "-"
    if ([string]::IsNullOrWhiteSpace($slug)) {
        return "upstream"
    }
    return $slug
}

if (-not (Test-Path -LiteralPath (Join-Path $RepoPath ".git"))) {
    throw "RepoPath is not a git repository: $RepoPath"
}

$topLevel = Get-GitOutput -Cwd $RepoPath -GitArgs @("rev-parse", "--show-toplevel")
$topLevel = (Resolve-Path -LiteralPath $topLevel).Path
$repoRoot = (Resolve-Path -LiteralPath $RepoPath).Path
if ($topLevel -ne $repoRoot) {
    Write-Host "Resolved repo root: $topLevel"
}

$status = Get-GitOutput -Cwd $topLevel -GitArgs @("status", "--porcelain=v1")
if ($status) {
    if ($PlanOnly) {
        Write-Host "WARN: Working tree is not clean. Actual upstream-sync worktree creation will stop until changes are committed or stashed."
    } else {
        throw "Working tree is not clean. Commit or stash changes before creating an upstream-sync worktree."
    }
}

Invoke-Git -Cwd $topLevel -GitArgs @("remote", "get-url", $ForkRemote) | Out-Null
Invoke-Git -Cwd $topLevel -GitArgs @("remote", "get-url", $UpstreamRemote) | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$upstreamRef = "$UpstreamRemote/$UpstreamBranch"

if (-not $PlanOnly) {
    Invoke-Git -Cwd $topLevel -GitArgs @("fetch", $UpstreamRemote, "--prune", "--tags")
    Invoke-Git -Cwd $topLevel -GitArgs @("fetch", $ForkRemote, "--prune")
}

$upstreamTag = ""
$describeOutput = & git -C $topLevel describe --tags --abbrev=0 $upstreamRef 2>$null
if ($LASTEXITCODE -eq 0) {
    $upstreamTag = ($describeOutput -join "`n").Trim()
}
if ([string]::IsNullOrWhiteSpace($upstreamTag)) {
    $upstreamTag = $UpstreamBranch
}

$upstreamSlug = ConvertTo-BranchSlug -Value $upstreamTag
if ([string]::IsNullOrWhiteSpace($ReviewBranch)) {
    $ReviewBranch = "codex/upstream-sync-v$upstreamSlug-$timestamp"
}
if ([string]::IsNullOrWhiteSpace($WorktreePath)) {
    $parent = Split-Path -Parent $topLevel
    $WorktreePath = Join-Path $parent "Cockpit-Tools-Local-upstream-sync-v$upstreamSlug-$timestamp"
}

$backupBranch = "backup/selfuse-pre-upstream-sync-$timestamp"

Write-Host "Self-use branch: $SelfUseBranch"
Write-Host "Official source: $upstreamRef"
Write-Host "Detected upstream tag: $upstreamTag"
Write-Host "Review branch: $ReviewBranch"
Write-Host "Review worktree: $WorktreePath"
Write-Host "Policy: keep self-use main; merge upstream in an isolated worktree; stop on conflicts."

if ($PlanOnly) {
    Write-Host ""
    Write-Host "Plan only. No changes were made."
    Write-Host "Would run:"
    Write-Host "  git fetch $UpstreamRemote --prune --tags"
    Write-Host "  git fetch $ForkRemote --prune"
    Write-Host "  git branch $backupBranch $SelfUseBranch"
    Write-Host "  git worktree add -b $ReviewBranch `"$WorktreePath`" $SelfUseBranch"
    Write-Host "  git -C `"$WorktreePath`" merge --no-ff $upstreamRef"
    if ($InstallDeps) {
        Write-Host "  npm ci"
    }
    if ($RunBuild) {
        Write-Host "  npm run build"
        Write-Host "  cargo test --manifest-path src-tauri/Cargo.toml --lib"
        Write-Host "  node scripts/release/preflight.cjs --skip-typecheck --skip-build --skip-cargo --skip-cargo-test"
    }
    if ($Push) {
        Write-Host "  git -C `"$WorktreePath`" push -u $ForkRemote $ReviewBranch"
    }
    return
}

if (Test-Path -LiteralPath $WorktreePath) {
    throw "WorktreePath already exists: $WorktreePath"
}

& git -C $topLevel show-ref --verify --quiet "refs/heads/$ReviewBranch"
$branchExists = $LASTEXITCODE
if ($branchExists -eq 0) {
    throw "Review branch already exists: $ReviewBranch"
}

Invoke-Git -Cwd $topLevel -GitArgs @("branch", $backupBranch, $SelfUseBranch)
Write-Host "Created local backup branch: $backupBranch"

Invoke-Git -Cwd $topLevel -GitArgs @("worktree", "add", "-b", $ReviewBranch, $WorktreePath, $SelfUseBranch)
Write-Host "Created review worktree."

try {
    Invoke-Git -Cwd $WorktreePath -GitArgs @(
        "merge",
        "--no-ff",
        $upstreamRef,
        "-m",
        "merge: absorb $upstreamRef into Cockpit Tools Local"
    )
} catch {
    Write-Host ""
    Write-Host "Merge stopped in the review worktree. Keep intentional self-use behavior unless upstream is clearly better."
    Write-Host "Review conflicts at:"
    Write-Host "  $WorktreePath"
    Write-Host "Continue after resolving:"
    Write-Host "  git -C `"$WorktreePath`" status"
    Write-Host "  git -C `"$WorktreePath`" merge --continue"
    Write-Host "Rollback this attempt:"
    Write-Host "  git -C `"$WorktreePath`" merge --abort"
    Write-Host "  git worktree remove `"$WorktreePath`""
    Write-Host "  git branch -D $ReviewBranch"
    throw
}

if ($InstallDeps) {
    Invoke-CommandChecked -WorkingDirectory $WorktreePath -FilePath npm -Arguments @("ci")
}

if ($RunBuild) {
    if (-not (Test-Path -LiteralPath (Join-Path $WorktreePath "node_modules"))) {
        throw "node_modules is missing in the review worktree. Re-run with -InstallDeps or install dependencies there before -RunBuild."
    }
    Invoke-CommandChecked -WorkingDirectory $WorktreePath -FilePath npm -Arguments @("run", "build")
    Invoke-CommandChecked -WorkingDirectory $WorktreePath -FilePath cargo -Arguments @(
        "test",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "--lib"
    )
    Invoke-CommandChecked -WorkingDirectory $WorktreePath -FilePath node -Arguments @(
        "scripts/release/preflight.cjs",
        "--skip-typecheck",
        "--skip-build",
        "--skip-cargo",
        "--skip-cargo-test"
    )
}

if ($Push) {
    Invoke-Git -Cwd $WorktreePath -GitArgs @("push", "-u", $ForkRemote, $ReviewBranch)
    Write-Host "Pushed review branch. Open a PR from $ReviewBranch to $SelfUseBranch for GitHub review/checks."
} else {
    Write-Host "Push was not requested. Review locally, then push with:"
    Write-Host "  git -C `"$WorktreePath`" push -u $ForkRemote $ReviewBranch"
}

Write-Host "Upstream-sync review branch is ready."
