#Requires -Version 7
<#
.SYNOPSIS
Native process-kill/resume harness for one elapsed SIMULATION_TEST mock block (#67 AC2).

.DESCRIPTION
Launches the Windows SimulationTest player with -simulationMockBlock, drives the
normal durable operator mailbox (load, start), waits until the planned item's cue
and audio request are durable, terminates the player process, relaunches it on the
same journal, explicitly starts it again, and waits for the resumed block to
finish and export. tools/mock_visit/kill_resume.py then verifies the journals.

The block uses synthetic MOCK-nn slots, a silent synthetic cue and mock readiness
gates. It is not a joined participant visit, acoustic test or headset test.
#>
param(
    [Parameter(Mandatory)][string]$Player,
    [Parameter(Mandatory)][string]$Capability,
    [Parameter(Mandatory)][string]$CapabilitySha256,
    [Parameter(Mandatory)][string]$RunRoot,
    [Parameter(Mandatory)][string]$Python,
    [ValidateRange(2,36)][int]$Items = 4,
    [ValidateRange(1,35)][int]$KillItem = 2,
    [int]$StartTimeoutSeconds = 180
)
$ErrorActionPreference = 'Stop'
if ($KillItem -ge $Items) { throw 'KillItem must leave at least one unplayed item to resume.' }
$Player = [IO.Path]::GetFullPath($Player); $Capability = [IO.Path]::GetFullPath($Capability); $RunRoot = [IO.Path]::GetFullPath($RunRoot)
if (Test-Path -LiteralPath $RunRoot) { throw 'Use a fresh run root inside the capability output_directory.' }
if ((Get-FileHash -LiteralPath $Capability -Algorithm SHA256).Hash.ToLowerInvariant() -ne $CapabilitySha256) { throw 'Capability bytes do not match the independent pin.' }
New-Item -ItemType Directory -Path $RunRoot | Out-Null
$repo = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
$mailbox = Join-Path $RunRoot 'mailbox'; $dataDir = Join-Path $RunRoot 'data'
$target = 'MOCK-{0:D2}' -f $KillItem
$log = Join-Path $RunRoot 'harness.log'
function Note([string]$text) { $line = '{0} {1}' -f [DateTime]::UtcNow.ToString('o'), $text; Add-Content -LiteralPath $log -Value $line; Write-Host $line }

function Start-Player([int]$n) {
    $arguments = @('-simulationTestConfig', ('"' + $Capability + '"'), '-simulationTestConfigSha256', $CapabilitySha256,
        '-simulationMockBlock', ('"' + $RunRoot + '"'), '-simulationMockBlockItems', $Items, '-simulationMockBlockQuit',
        '-logFile', ('"' + (Join-Path $RunRoot "player-$n.log") + '"'))
    $p = Start-Process -FilePath $Player -ArgumentList $arguments -PassThru
    Note "launched process $n pid=$($p.Id)"; return $p
}
function Read-Json([string]$path) {
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $stream = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
            try { $reader = [IO.StreamReader]::new($stream); return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $stream.Dispose() }
        } catch [IO.IOException] { Start-Sleep -Milliseconds 20 }
    }
    throw "Cannot read $path"
}
function Wait-State([object]$process, [string]$notNonce) {
    $deadline = [DateTime]::UtcNow.AddSeconds($StartTimeoutSeconds); $statePath = Join-Path $mailbox 'state.json'
    while ([DateTime]::UtcNow -lt $deadline) {
        $process.Refresh(); if ($process.HasExited) { throw "Player exited during startup with code $($process.ExitCode); see its log." }
        if (Test-Path -LiteralPath $statePath) { $s = Read-Json $statePath; if ($s.session_nonce -ne $notNonce) { return $s } }
        Start-Sleep -Milliseconds 200
    }
    throw 'Player mailbox state did not appear; see the player log (startup, capability or XR refusal).'
}
function Send-Command([object]$state, [int]$sequence, [string]$command) {
    $request = [ordered]@{ version = 1; session_nonce = $state.session_nonce; request_id = [Guid]::NewGuid().ToString('N'); sequence = $sequence
        command = $command; run_sheet_manifest_sha256 = $state.run_sheet_manifest_sha256; schedule_sha256 = $state.schedule_sha256 }
    $json = $request | ConvertTo-Json -Compress
    $temporary = Join-Path $mailbox ('command.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination (Join-Path $mailbox 'command.json') -Force
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $s = Read-Json (Join-Path $mailbox 'state.json')
        if ($s.receipt -and $s.receipt.request_id -eq $request.request_id) {
            Note "command $command seq=$sequence status=$($s.receipt.status) code=$($s.receipt.code)"
            if ($s.receipt.status -ne 'accepted') { throw "Operator command $command was rejected: $($s.receipt.code)" }
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "No receipt for operator command $command"
}
function Read-Rows {
    $rows = @()
    foreach ($segment in (Get-ChildItem -LiteralPath $dataDir -Filter 'events-*.local.jsonl' | Sort-Object Name)) {
        $stream = [IO.FileStream]::new($segment.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try { $text = [IO.StreamReader]::new($stream).ReadToEnd() } finally { $stream.Dispose() }
        $lines = $text -split "`n"
        # A final fragment without a newline is not yet a durable record.
        for ($i = 0; $i -lt $lines.Count - 1; $i++) { if ($lines[$i].Length -gt 0) { $rows += ($lines[$i] | ConvertFrom-Json -Depth 32) } }
    }
    return , $rows
}
function Start-Record([string]$nonce) { Read-Json (Join-Path $RunRoot "mock-block-start-$nonce.local.json") }

$first = Start-Player 1; $second = $null
try {
    $state = Wait-State $first ''
    Send-Command $state 1 'load'; Send-Command $state 2 'start'
    $deadline = [DateTime]::UtcNow.AddSeconds(14 * $KillItem + 60); $observed = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        $first.Refresh(); if ($first.HasExited) { throw "First player exited before the kill point with code $($first.ExitCode)." }
        $rows = Read-Rows
        $requested = @($rows | Where-Object { $_.event_type -eq 'audio_request' -and $_.opportunity_id -eq $target })
        $done = @($rows | Where-Object { $_.event_type -eq 'session' -and $_.opportunity_id -eq $target -and $_.payload.state -eq 'Done' })
        if ($requested.Count -gt 0 -and $done.Count -eq 0) { $observed = $rows[-1]; break }
        if ($done.Count -gt 0) { throw "Kill point passed: $target finished before it was observed open." }
        Start-Sleep -Milliseconds 50
    }
    if ($null -eq $observed) { throw "The planned cue for $target was not observed in time." }
    $firstStart = Start-Record $state.session_nonce
    # Terminate the actual player process (TerminateProcess). No shutdown hook runs.
    Stop-Process -Id $first.Id -Force; $killUtc = [DateTime]::UtcNow.ToString('o')
    $first.WaitForExit(); $first.Refresh(); $firstExit = $first.ExitCode
    Note "killed pid=$($first.Id) exit=$firstExit after record sequence=$($observed.sequence) target=$target"
    Remove-Item -LiteralPath (Join-Path $mailbox 'command.json') -ErrorAction SilentlyContinue

    $second = Start-Player 2
    $resumedState = Wait-State $second $state.session_nonce
    Send-Command $resumedState 1 'load'; Send-Command $resumedState 2 'start'
    if (!$second.WaitForExit((14 * ($Items - $KillItem) + 120) * 1000)) { throw 'Resumed player did not finish the block in time.' }
    $second.Refresh(); $secondExit = $second.ExitCode; Note "resumed pid=$($second.Id) exit=$secondExit"
    $secondStart = Start-Record $resumedState.session_nonce
    $result = Read-Json (Join-Path $RunRoot "mock-block-result-$($resumedState.session_nonce).local.json")

    $receipt = [ordered]@{ version = 1; scope = 'SIMULATION_TEST'; kind = 'process_kill_resume'; items = $Items; kill_target_opportunity = $target
        killed = [ordered]@{ process_id = $first.Id; session_nonce = $state.session_nonce; data_clock_epoch = $firstStart.data_clock_epoch; exit_code = $firstExit
            kill_utc = $killUtc; last_observed_record_sha256 = $observed.sha256; last_observed_record_count = [int]$observed.sequence + 1 }
        resumed = [ordered]@{ process_id = $second.Id; session_nonce = $resumedState.session_nonce; data_clock_epoch = $secondStart.data_clock_epoch; exit_code = $secondExit; result_status = $result.status } }
    $receiptPath = Join-Path $RunRoot 'kill-receipt.json'
    [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 8) + "`n", [Text.UTF8Encoding]::new($false))
    $receiptSha = (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Push-Location $repo
    try { & $Python -m tools.mock_visit.kill_resume --run-root $RunRoot --receipt $receiptPath --receipt-sha256 $receiptSha --out (Join-Path $RunRoot 'kill-resume-verification.json'); $verifierExit = $LASTEXITCODE } finally { Pop-Location }
    Note "verifier exit=$verifierExit receipt_sha256=$receiptSha"
    if ($verifierExit -ne 0) { throw 'Kill/resume verification did not pass; inspect kill-resume-verification.json.' }
} finally {
    foreach ($p in @($first, $second)) { if ($p) { $p.Refresh(); if (!$p.HasExited) { Stop-Process -Id $p.Id -Force; Note "stopped owned pid=$($p.Id) during cleanup" } } }
}
