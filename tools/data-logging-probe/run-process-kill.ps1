param([Parameter(Mandatory)][string]$ProbeDll,[Parameter(Mandatory)][string]$FreshRawDirectory)
$ErrorActionPreference='Stop'
if(Test-Path -LiteralPath $FreshRawDirectory){throw 'Use a fresh private directory'}
$probeArgs='"'+[IO.Path]::GetFullPath($ProbeDll)+'" write "'+[IO.Path]::GetFullPath($FreshRawDirectory)+'"'
$owned=Start-Process -FilePath 'dotnet' -ArgumentList $probeArgs -WindowStyle Hidden -PassThru -RedirectStandardOutput ($FreshRawDirectory+'.out') -RedirectStandardError ($FreshRawDirectory+'.err')
try {
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while (!(Test-Path -LiteralPath ($FreshRawDirectory+'.ready')) -and !$owned.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100; $owned.Refresh() }
    if (!(Test-Path -LiteralPath ($FreshRawDirectory+'.ready'))) { throw 'Probe did not acknowledge durable record; inspect retained stderr' }
    $segment=Join-Path $FreshRawDirectory 'events-0000.local.jsonl'
    $inputFile=[IO.FileStream]::new($segment,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    $hash=[Security.Cryptography.SHA256]::Create()
    try {$before=[Convert]::ToHexString($hash.ComputeHash($inputFile)).ToLowerInvariant()} finally {$inputFile.Dispose();$hash.Dispose()}
    Stop-Process -Id $owned.Id
    $owned.WaitForExit()
    & dotnet $ProbeDll verify $FreshRawDirectory
    $verificationExit=$LASTEXITCODE
    $after=(Get-FileHash -LiteralPath $segment -Algorithm SHA256).Hash.ToLowerInvariant()
    $result=[pscustomobject]@{qualification='synthetic_writer_process_kill_not_device_runtime';process_exit_code=$owned.ExitCode;verification_exit=$verificationExit;segment_before_sha256=$before;segment_after_sha256=$after;unchanged=($before -eq $after)}
    $result | ConvertTo-Json | Set-Content -LiteralPath ($FreshRawDirectory+'.result.json')
    $result
    if($verificationExit -ne 0 -or $before -ne $after){throw 'Recovery verification failed'}
} finally { $owned.Refresh();if(!$owned.HasExited){Stop-Process -Id $owned.Id};$owned.Dispose() }
