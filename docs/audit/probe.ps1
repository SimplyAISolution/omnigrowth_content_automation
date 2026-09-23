# OmniGrowth Phase 0 discovery probe (read-only).
# Usage: pwsh -NoProfile -File docs/audit/probe.ps1 > docs/audit/probe_output.txt 2>&1
# Every section is prefixed with "### <NAME>" so PHASE0_AUDIT.md can cite it.

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Section($name) { Write-Output ""; Write-Output "### $name" }

function Try-Cmd($label, [scriptblock]$block) {
    try {
        $out = & $block 2>&1 | Out-String
        Write-Output "[$label] $($out.TrimEnd())"
    } catch {
        Write-Output "[$label] ERROR: $($_.Exception.Message)"
    }
}

function Probe-Models($label, $url) {
    try {
        $resp = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        Write-Output "[$label] HTTP $($resp.StatusCode)"
        try {
            $json = $resp.Content | ConvertFrom-Json
            $ids = @($json.data | ForEach-Object { $_.id })
            Write-Output "[$label] model_count=$($ids.Count)"
            foreach ($id in $ids) { Write-Output "[$label] model: $id" }
        } catch {
            Write-Output "[$label] body (non-JSON, first 500 chars): $($resp.Content.Substring(0, [Math]::Min(500, $resp.Content.Length)))"
        }
    } catch {
        Write-Output "[$label] UNREACHABLE: $($_.Exception.Message)"
    }
}

Write-Output "OmniGrowth Phase 0 probe"
Write-Output "timestamp_utc: $((Get-Date).ToUniversalTime().ToString('o'))"
Write-Output "repo_root: $RepoRoot"

Section "OS"
Try-Cmd "os_caption" { (Get-CimInstance Win32_OperatingSystem).Caption }
Try-Cmd "os_version" { (Get-CimInstance Win32_OperatingSystem).Version }
Try-Cmd "os_build"   { (Get-CimInstance Win32_OperatingSystem).BuildNumber }
Try-Cmd "arch"       { $env:PROCESSOR_ARCHITECTURE }
Try-Cmd "cpu"        { (Get-CimInstance Win32_Processor | Select-Object -First 1).Name }
Try-Cmd "ram_gb"     { [Math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1) }

Section "SHELL"
Try-Cmd "ps_edition" { $PSVersionTable.PSEdition }
Try-Cmd "ps_version" { $PSVersionTable.PSVersion.ToString() }
Try-Cmd "comspec"    { $env:ComSpec }

Section "TOOL_VERSIONS"
foreach ($tool in @('python', 'py', 'uv', 'node', 'npm', 'git', 'docker', 'wsl')) {
    $cmd = Get-Command $tool -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { Write-Output "[$tool] NOT_FOUND on PATH"; continue }
    Write-Output "[$tool] path: $($cmd.Source)"
}
Try-Cmd "python --version" { python --version }
Try-Cmd "py -0p"           { py -0p }
Try-Cmd "uv --version"     { uv --version }
Try-Cmd "uv python list"   { uv python list --only-installed }
Try-Cmd "node --version"   { node --version }
Try-Cmd "npm --version"    { npm --version }
Try-Cmd "git --version"    { git --version }
Try-Cmd "docker --version" { docker --version }
Try-Cmd "docker compose version" { docker compose version }
Try-Cmd "wsl --version"    { (wsl --version) -replace "`0", '' }
Try-Cmd "wsl -l -v"        { (wsl -l -v) -replace "`0", '' }

Section "DOCKER_STATE"
Try-Cmd "docker_desktop_process" { Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue | Select-Object -Property Name, Id | Out-String }
Try-Cmd "docker info (server)" { docker info --format '{{.ServerVersion}} | OS={{.OperatingSystem}} | CPUs={{.NCPU}} | Mem={{.MemTotal}} | Containers={{.Containers}} running={{.ContainersRunning}}' }
Try-Cmd "docker ps" { docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' }

Section "DISK"
Try-Cmd "drives" { Get-PSDrive -PSProvider FileSystem | ForEach-Object { "{0}: free_gb={1} used_gb={2}" -f $_.Name, [Math]::Round($_.Free / 1GB, 1), [Math]::Round($_.Used / 1GB, 1) } }

Section "GPU"
Try-Cmd "nvidia-smi" { nvidia-smi }
Try-Cmd "nvidia-smi query" { nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv }

Section "LOCAL_LLM"
Probe-Models "lmstudio" "http://127.0.0.1:1234/v1/models"
Probe-Models "ollama"   "http://127.0.0.1:11434/v1/models"

Section "PORTS"
foreach ($p in @(1234, 11434, 5432, 6379, 8000, 5678, 5173)) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $proc = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Write-Output "[port $p] LISTEN pid=$($c.OwningProcess) proc=$proc"
    } else { Write-Output "[port $p] free" }
}

Section "ENV_VARS"
# Names only, never values (may contain secrets).
Try-Cmd "relevant_env_names" {
    Get-ChildItem Env: | Where-Object { $_.Name -match 'OMNIGROWTH|OPENAI|ANTHROPIC|LMSTUDIO|OLLAMA|PUBLISH|DATABASE|REDIS|UV_|VIRTUAL_ENV|DOCKER' } |
        ForEach-Object { $_.Name }
}

Section "GIT_STATE"
Push-Location $RepoRoot
Try-Cmd "git rev-parse" { git rev-parse --is-inside-work-tree }
Try-Cmd "git status"    { git status --porcelain }
Try-Cmd "git log"       { git log --oneline -5 }
Try-Cmd "git config user.name" { git config user.name }
Pop-Location

Section "FILE_INVENTORY"
Get-ChildItem -Path $RepoRoot -Recurse -File -Force |
    Where-Object { $_.FullName -notmatch '\\\.git\\' } |
    ForEach-Object { "{0,8}  {1}" -f $_.Length, $_.FullName.Substring($RepoRoot.Length + 1) }

Section "END"
Write-Output "probe complete"
