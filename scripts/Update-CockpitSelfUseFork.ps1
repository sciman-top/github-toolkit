param(
    [string]$RepoPath = "D:\CODE\external\cockpit-tools",
    [string]$SelfUseBranch = "codex/follow-current-local-v0.23.4",
    [string]$ForkRemote = "fork",
    [string]$UpstreamRemote = "origin",
    [string]$UpstreamBranch = "main",
    [switch]$PlanOnly,
    [switch]$RunBuild,
    [switch]$Push
)

$ErrorActionPreference = "Stop"

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-CommandChecked {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $RepoPath ".git"))) {
    throw "RepoPath is not a git repository: $RepoPath"
}

Push-Location $RepoPath
try {
    $topLevel = (& git rev-parse --show-toplevel).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($topLevel)) {
        throw "Unable to resolve git top-level for $RepoPath"
    }
    if ((Resolve-Path -LiteralPath $topLevel).Path -ne (Resolve-Path -LiteralPath $RepoPath).Path) {
        Write-Host "Resolved repo root: $topLevel"
        Set-Location $topLevel
    }

    $status = (& git status --porcelain=v1)
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed"
    }
    if ($status) {
        throw "Working tree is not clean. Commit or stash changes before updating the self-use branch."
    }

    Write-Host "Self-use branch: $SelfUseBranch"
    Write-Host "Upstream target: $UpstreamRemote/$UpstreamBranch"
    Write-Host "Fork remote: $ForkRemote"
    Write-Host "Policy: keep self-use changes, replay them onto upstream, stop on conflicts."

    Invoke-Git remote get-url $ForkRemote | Out-Null
    Invoke-Git remote get-url $UpstreamRemote | Out-Null

    if ($PlanOnly) {
        Write-Host ""
        Write-Host "Plan only. No changes were made."
        Write-Host "Would run:"
        Write-Host "  git fetch $UpstreamRemote"
        Write-Host "  git fetch $ForkRemote"
        Write-Host "  git switch $SelfUseBranch"
        Write-Host "  git branch backup/selfuse-pre-rebase-<timestamp> HEAD"
        Write-Host "  git rebase $UpstreamRemote/$UpstreamBranch"
        if ($RunBuild) {
            Write-Host "  npm run typecheck"
            Write-Host "  npm run build"
        }
        if ($Push) {
            Write-Host "  git push $ForkRemote ${SelfUseBranch}:$SelfUseBranch --force-with-lease"
        }
        return
    }

    Invoke-Git fetch $UpstreamRemote
    Invoke-Git fetch $ForkRemote
    Invoke-Git switch $SelfUseBranch

    $backupBranch = "backup/selfuse-pre-rebase-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Invoke-Git branch $backupBranch HEAD
    Write-Host "Created local backup branch: $backupBranch"

    try {
        Invoke-Git rebase "$UpstreamRemote/$UpstreamBranch"
    } catch {
        Write-Host ""
        Write-Host "Rebase stopped. Resolve conflicts by keeping self-use behavior where it is intentional,"
        Write-Host "while accepting compatible upstream fixes and new features."
        Write-Host "Then run:"
        Write-Host "  git status"
        Write-Host "  git rebase --continue"
        Write-Host "Or rollback:"
        Write-Host "  git rebase --abort"
        Write-Host "  git switch $SelfUseBranch"
        Write-Host "  git reset --hard $backupBranch"
        throw
    }

    if ($RunBuild) {
        Invoke-CommandChecked npm @("run", "typecheck")
        Invoke-CommandChecked npm @("run", "build")
    }

    if ($Push) {
        Invoke-Git push $ForkRemote "${SelfUseBranch}:$SelfUseBranch" --force-with-lease
    } else {
        Write-Host "Push was not requested. Review locally, then push with:"
        Write-Host "  git push $ForkRemote ${SelfUseBranch}:$SelfUseBranch --force-with-lease"
    }

    Write-Host "Self-use branch update complete."
} finally {
    Pop-Location
}
