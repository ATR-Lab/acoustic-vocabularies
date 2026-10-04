param(
    [Parameter(Mandatory)][string]$Unity,
    [Parameter(Mandatory)][ValidateSet('Configure','Test','Android','Windows')][string]$Target,
    [Parameter(Mandatory)][string]$ProtocolVersion,
    [Parameter(Mandatory)][string]$BuildId,
    [switch]$AllowDirty,
    [string]$TemporaryDirectory,
    [string]$GradleCache
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$project = Join-Path $repo 'unity'
$output = Join-Path $repo ('.local/foundation/' + $BuildId)
if ($BuildId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { throw 'Invalid build identifier' }
if ($ProtocolVersion -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') { throw 'Invalid protocol version' }
$revision = (& git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Git commit' }
$dirty = [bool](& git -C $repo status --porcelain)
if ($dirty -and -not $AllowDirty) { throw 'Working tree is dirty. Commit reviewed source or explicitly mark a local engineering build with -AllowDirty.' }
New-Item -ItemType Directory -Path $output -Force | Out-Null
$environment = @{ EXPERIMENT_PROTOCOL_VERSION=$ProtocolVersion; EXPERIMENT_BUILD_ID=$BuildId; EXPERIMENT_COMMIT_SHA=$revision; EXPERIMENT_DIRTY_SOURCE=$dirty.ToString().ToLowerInvariant() }
if ($TemporaryDirectory) { New-Item -ItemType Directory -Path $TemporaryDirectory -Force | Out-Null; $environment.TEMP=$TemporaryDirectory; $environment.TMP=$TemporaryDirectory }
if ($GradleCache) { New-Item -ItemType Directory -Path $GradleCache -Force | Out-Null; $environment.GRADLE_USER_HOME=$GradleCache }
$log = Join-Path $output ($Target + '.log')
if (Test-Path -LiteralPath $log) { throw 'Use a fresh build identifier; existing logs will not be overwritten.' }
$unityArguments = @('-batchmode','-nographics','-projectPath',('"'+$project+'"'),'-logFile',('"'+$log+'"'))
if ($Target -eq 'Test') {
    $results = Join-Path $output 'editmode.xml'
    $unityArguments += @('-runTests','-testPlatform','EditMode','-assemblyNames','AcousticVocab.Foundation.Tests','-testResults',('"'+$results+'"'))
} else {
    $method = if ($Target -eq 'Configure') { 'Configure' } else { 'Build'+$Target }
    $unityArguments += @('-quit','-executeMethod',('AcousticVocab.Foundation.Editor.FoundationBuild.'+$method))
}
$process = Start-Process -FilePath $Unity -ArgumentList $unityArguments -Environment $environment -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Unity failed with exit code $($process.ExitCode). Inspect the private build log." }
if ($Target -eq 'Test') {
    [xml]$result = Get-Content -Raw -LiteralPath $results
    if ($result.'test-run'.result -ne 'Passed' -or [int]$result.'test-run'.total -lt 1) { throw 'Unity edit-mode suite did not pass or discovered zero tests.' }
}
if ($Target -in @('Android','Windows')) {
    $binary = if ($Target -eq 'Android') { 'experiment.apk' } else { 'experiment.exe' }
    $buildDirectory = Join-Path $project "Builds/$BuildId/$Target"
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $buildDirectory ($binary+'.build.json')) | ConvertFrom-Json
    if ($manifest.result -ne 'Succeeded' -or $manifest.build_identity.commit_sha -ne $revision -or $manifest.build_identity.build_id -ne $BuildId -or $manifest.files.Count -lt 1) { throw 'Build record or identity mismatch.' }
    foreach ($entry in $manifest.files) {
        $file = Join-Path $buildDirectory $entry.path
        if ((Get-Item -LiteralPath $file).Length -ne $entry.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant() -ne $entry.sha256) { throw 'Built file failed manifest verification.' }
    }
}
Write-Output "Foundation $Target completed. Identity=$BuildId; commit=$revision; dirty=$dirty"
