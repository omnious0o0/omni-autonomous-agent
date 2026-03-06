$ErrorActionPreference = "Stop"

function Write-Section($title) {
  Write-Output "----------------------------------------------------------------------"
  Write-Output "  $title"
  Write-Output "----------------------------------------------------------------------"
}

function Get-PythonCommand {
  foreach ($name in @("python", "python3", "py")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) {
      continue
    }

    try {
      $versionOutput = & $cmd.Source --version 2>&1
    }
    catch {
      continue
    }

    if ($versionOutput -match "Python 3\.(\d+)") {
      return $cmd
    }
  }

  return $null
}

function Refresh-Path {
  $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $segments = @()
  if ($machinePath) { $segments += $machinePath }
  if ($userPath) { $segments += $userPath }
  if ($segments.Count -gt 0) {
    $env:Path = ($segments -join ";")
  }
}

function Get-PowerShellHostCommand {
  foreach ($name in @("pwsh", "powershell")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
      return $cmd
    }
  }

  try {
    $currentProcess = Get-Process -Id $PID -ErrorAction Stop
    if ($currentProcess.Path) {
      return @{
        Source = $currentProcess.Path
      }
    }
  }
  catch {
  }

  throw "could not locate a PowerShell executable for bootstrap verification"
}

function Ensure-WingetCommand {
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($winget) {
    return $winget
  }

  throw "winget is required to self-install missing dependencies on Windows."
}

function Ensure-PythonCommand {
  $python = Get-PythonCommand
  if ($python) {
    return $python
  }

  Write-Section "Installing Python runtime"
  $winget = Ensure-WingetCommand
  & $winget.Source install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
  Refresh-Path

  $python = Get-PythonCommand
  if (-not $python) {
    throw "python installation did not succeed"
  }

  return $python
}

function Get-GitCommand {
  return Get-Command git -ErrorAction SilentlyContinue
}

function Ensure-GitCommand {
  $git = Get-GitCommand
  if ($git) {
    return $git
  }

  Write-Section "Installing git"
  $winget = Ensure-WingetCommand
  & $winget.Source install --id Git.Git --silent --accept-source-agreements --accept-package-agreements
  Refresh-Path

  $git = Get-GitCommand
  if (-not $git) {
    throw "git installation did not succeed"
  }

  return $git
}

$scriptSourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Resolve-Path (Join-Path $scriptSourceDir "..")
$mainScript = Join-Path $rootDir "main.py"
$repoUrl = if ($env:OMNI_AGENT_REPO_URL -and $env:OMNI_AGENT_REPO_URL.Trim()) { $env:OMNI_AGENT_REPO_URL.Trim() } else { "https://github.com/omnious0o0/omni-autonomous-agent.git" }
$installDir = $env:OMNI_AGENT_INSTALL_DIR
if (-not $installDir -or -not $installDir.Trim()) {
  $installDir = Join-Path $HOME ".omni-autonomous-agent"
}

$repoGitDir = Join-Path $installDir ".git"
$installScript = Join-Path $installDir ".omni-autonomous-agent\install.ps1"

if (-not (Test-Path $mainScript)) {
  Write-Section "Bootstrapping repository"

  $git = Ensure-GitCommand
  $null = Ensure-PythonCommand

  if (Test-Path $repoGitDir) {
    Write-Output "  Repository:  $installDir (existing, pulling latest)"
    & $git.Source -C $installDir pull --ff-only
    if ($LASTEXITCODE -ne 0) {
      throw "failed to pull latest repository at $installDir"
    }
  }
  elseif (Test-Path $installDir) {
    throw "$installDir exists but is not a git repository. Remove it manually or set OMNI_AGENT_INSTALL_DIR to a clean location."
  }
  else {
    Write-Output "  Repository:  Cloning to $installDir"
    & $git.Source clone $repoUrl $installDir
    if ($LASTEXITCODE -ne 0) {
      throw "failed to clone repository to $installDir"
    }
  }

  if (-not (Test-Path $installScript)) {
    throw "install script not found at $installScript after bootstrap"
  }

  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($pwsh) {
    & $pwsh.Source -NoProfile -ExecutionPolicy Bypass -File $installScript
  }
  else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installScript
  }
  exit $LASTEXITCODE
}

$pythonCommand = Ensure-PythonCommand

$destName = "omni-autonomous-agent"
$localBin = $env:OMNI_AGENT_LOCAL_BIN
if (-not $localBin -or -not $localBin.Trim()) {
  if ($env:LOCALAPPDATA -and $env:LOCALAPPDATA.Trim()) {
    $localBin = Join-Path $env:LOCALAPPDATA "omni-autonomous-agent\bin"
  }
  else {
    $localBin = Join-Path $HOME "AppData\Local\omni-autonomous-agent\bin"
  }
}

New-Item -ItemType Directory -Force -Path $localBin | Out-Null

$runnerPs1 = Join-Path $localBin "$destName.ps1"
$runnerCmd = Join-Path $localBin "$destName.cmd"

$pythonExecutable = $pythonCommand.Source -replace "'", "''"
$mainScriptEscaped = $mainScript -replace "'", "''"
$runnerPs1Body = @"
`$ErrorActionPreference = "Stop"
`$python = '$pythonExecutable'
`$mainScript = '$mainScriptEscaped'

& `$python `$mainScript @args
exit `$LASTEXITCODE
"@

$runnerCmdBody = @"
@echo off
setlocal
set "PS_EXE=pwsh"
where pwsh >nul 2>&1 || set "PS_EXE=powershell"
%PS_EXE% -NoProfile -ExecutionPolicy Bypass -File "$runnerPs1" %*
exit /b %ERRORLEVEL%
"@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($runnerPs1, $runnerPs1Body, $utf8NoBom)
[System.IO.File]::WriteAllText($runnerCmd, $runnerCmdBody, $utf8NoBom)

Write-Section "Installed"
Write-Output "  Source:      $mainScript"
Write-Output "  Command:     $runnerCmd"
Write-Output "  Self-check:  Read $rootDir\install-help.md"

$bootstrapTimeoutRaw = [string]$env:OMNI_AGENT_BOOTSTRAP_TIMEOUT
$bootstrapTimeout = 120
$parsedBootstrapTimeout = 0
if ($bootstrapTimeoutRaw -and [int]::TryParse($bootstrapTimeoutRaw, [ref]$parsedBootstrapTimeout) -and $parsedBootstrapTimeout -gt 0) {
  $bootstrapTimeout = $parsedBootstrapTimeout
}

$bootstrapStdout = [System.IO.Path]::GetTempFileName()
$bootstrapStderr = [System.IO.Path]::GetTempFileName()

try {
  $bootstrapFilePath = ""
  $bootstrapArguments = @()

  if ($env:ComSpec -and $env:ComSpec.Trim()) {
    $bootstrapFilePath = $env:ComSpec
    $bootstrapArguments = @("/d", "/c", "`"$runnerCmd`" --bootstrap")
  }
  else {
    $pwshHost = Get-PowerShellHostCommand
    $bootstrapFilePath = $pwshHost.Source
    $bootstrapArguments = @("-NoLogo", "-NoProfile", "-File", $runnerPs1, "--bootstrap")
  }

  $bootstrapProcess = Start-Process `
    -FilePath $bootstrapFilePath `
    -ArgumentList $bootstrapArguments `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $bootstrapStdout `
    -RedirectStandardError $bootstrapStderr

  Wait-Process -Id $bootstrapProcess.Id -Timeout $bootstrapTimeout -ErrorAction SilentlyContinue
  $bootstrapProcess.Refresh()

  if (-not $bootstrapProcess.HasExited) {
    Stop-Process -Id $bootstrapProcess.Id -Force -ErrorAction SilentlyContinue
    throw "bootstrap timed out after ${bootstrapTimeout}s. Run '$runnerCmd --bootstrap' and fix reported warnings before autonomous use."
  }

  if ($bootstrapProcess.ExitCode -ne 0) {
    $stderr = (Get-Content $bootstrapStderr -Raw -ErrorAction SilentlyContinue)
    $stdout = (Get-Content $bootstrapStdout -Raw -ErrorAction SilentlyContinue)
    $details = ($stderr + "`n" + $stdout).Trim()
    if ($details) {
      throw "bootstrap did not complete successfully: $details"
    }
    throw "bootstrap did not complete successfully. Run '$runnerCmd --bootstrap' and fix reported warnings before autonomous use."
  }
}
finally {
  Remove-Item $bootstrapStdout -ErrorAction SilentlyContinue
  Remove-Item $bootstrapStderr -ErrorAction SilentlyContinue
}

Write-Output "  Bootstrap:   Completed automatic hook/setup bootstrap"
Write-Output ""
Write-Output "Run $destName --status to verify."
Write-Output "Read install-help.md and apply the self-check hook guidance before autonomous sessions."

$pathValue = [string]$env:PATH
if ($pathValue -notmatch [regex]::Escape($localBin)) {
  Write-Output ""
  Write-Output "Warning: $localBin is not on PATH."
  Write-Output "Add this line in PowerShell profile:"
  Write-Output "  `$env:PATH = '$localBin;' + `$env:PATH"
}

Write-Output "----------------------------------------------------------------------"
