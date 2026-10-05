param(
    [Parameter(Mandatory)][string]$Unity,
    [Parameter(Mandatory)][ValidateSet('Configure','Test','TestPlayMode','Android','Windows')][string]$Target,
    [Parameter(Mandatory)][string]$ProtocolVersion,
    [Parameter(Mandatory)][string]$BuildId,
    [ValidateSet('Foundation','Workcell','StateSources','Calibration','ResponsePanel','Teaching','Assessment','SelectionMenus','JoinedEngineering','FrameBudget','FrameProbe','Orientation','PreallocationEngineering')][string]$Scene = 'Foundation',
    [string]$G1Description,
    [switch]$GraphicsTests,
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
if ($Scene -in @('Workcell','StateSources','ResponsePanel','Teaching','Assessment','SelectionMenus','JoinedEngineering','FrameBudget','FrameProbe','Orientation','PreallocationEngineering')) {
    if (-not $G1Description -or -not (Test-Path -LiteralPath $G1Description -PathType Leaf)) { throw 'Workcell requires the reviewed converted G1 description via -G1Description.' }
    $environment.G1_DESCRIPTION_JSON=(Resolve-Path -LiteralPath $G1Description).Path
}
if ($TemporaryDirectory) { New-Item -ItemType Directory -Path $TemporaryDirectory -Force | Out-Null; $environment.TEMP=$TemporaryDirectory; $environment.TMP=$TemporaryDirectory }
if ($GradleCache) { New-Item -ItemType Directory -Path $GradleCache -Force | Out-Null; $environment.GRADLE_USER_HOME=$GradleCache }
$log = Join-Path $output ($Target + '.log')
if (Test-Path -LiteralPath $log) { throw 'Use a fresh build identifier; existing logs will not be overwritten.' }
if ($GraphicsTests -and $Target -ne 'TestPlayMode') { throw 'GraphicsTests is valid only for TestPlayMode' }
$unityArguments = @('-batchmode','-projectPath',('"'+$project+'"'),'-logFile',('"'+$log+'"'))
if (-not $GraphicsTests) { $unityArguments += '-nographics' }
if ($Target -eq 'Android') { $unityArguments += @('-buildTarget','Android') }
if ($Target -eq 'Windows') { $unityArguments += @('-buildTarget','Win64') }
if ($Target -in @('Test','TestPlayMode')) {
    $platform = if ($Target -eq 'Test') { 'EditMode' } else { 'PlayMode' }
    $results = Join-Path $output ($platform.ToLowerInvariant() + '.xml')
    $testAssemblies = @(Get-ChildItem -LiteralPath (Join-Path $project 'Assets') -Recurse -Filter '*.asmdef' | ForEach-Object {
        $definition = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
        $editorOnly = $definition.includePlatforms -contains 'Editor'
        if (($definition.optionalUnityReferences -contains 'TestAssemblies') -and
            (($platform -eq 'EditMode' -and $editorOnly) -or ($platform -eq 'PlayMode' -and -not $editorOnly))) {
            if ($definition.name -notmatch '^AcousticVocab\.[A-Za-z0-9.]+$') { throw 'Unexpected test assembly name' }
            $definition.name
        }
    })
    if ($testAssemblies.Count -lt 1) { throw 'No project test assemblies found for target platform' }
    $unityArguments += @('-runTests','-testPlatform',$platform,'-assemblyNames',($testAssemblies -join ';'),'-testResults',('"'+$results+'"'))
    $unityArguments += @('-testCategory',$(if ($GraphicsTests) { 'GraphicsRequired' } else { '!GraphicsRequired' }))
} else {
    $method = if ($Target -eq 'Configure') { 'Configure' } else { 'Build'+$Target }
    if ($Scene -eq 'FrameProbe' -and $Target -eq 'Android') { throw 'Engineering probe is a Windows-only diagnostic' }
    if ($Scene -eq 'FrameProbe' -and $Target -eq 'Windows') { $method = 'BuildProbeWindows' }
    if ($Scene -eq 'FrameProbe' -and $Target -eq 'Configure') { $method = 'ConfigureProbe' }
    $builder = switch ($Scene) { 'PreallocationEngineering' { 'AcousticVocab.SessionIntegration.Editor.PreallocationBuild.' } 'Orientation' { 'AcousticVocab.Orientation.Editor.OrientationBuild.' } 'JoinedEngineering' { 'AcousticVocab.SessionIntegration.Editor.JoinedEngineeringBuild.' } 'SelectionMenus' { 'AcousticVocab.SelectionMenus.Editor.MenuBuild.' } 'FrameBudget' { 'AcousticVocab.FrameBudget.Editor.FrameBudgetBuild.' } 'FrameProbe' { 'AcousticVocab.FrameBudget.Editor.FrameBudgetBuild.' } 'Assessment' { 'AcousticVocab.Assessment.Editor.AssessmentBuild.' } 'Teaching' { 'AcousticVocab.Teaching.Editor.TeachingBuild.' } 'Calibration' { 'AcousticVocab.StudyAudio.Editor.AudioBuild.' } 'ResponsePanel' { 'AcousticVocab.ResponsePanel.Editor.ResponsePanelBuild.' } 'Workcell' { 'AcousticVocab.Workcell.Editor.WorkcellBuild.' } 'StateSources' { 'AcousticVocab.StateIntegration.Editor.StateSourceBuild.' } default { 'AcousticVocab.Foundation.Editor.FoundationBuild.' } }
    $unityArguments += @('-quit','-executeMethod',($builder+$method))
}
$process = Start-Process -FilePath $Unity -ArgumentList $unityArguments -Environment $environment -WindowStyle Hidden -PassThru
# Wait for this editor's native exit code, not persistent shared helper descendants.
# Start-Process -Wait waits the entire process tree (PowerShell documentation).
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw "Unity failed with exit code $($process.ExitCode). Inspect the private build log." }
if ($Target -in @('Test','TestPlayMode')) {
    [xml]$result = Get-Content -Raw -LiteralPath $results
    $optionalRecordedChecks = @(
        'AcousticVocab.Tests.RecordedIsaacContractTests.ActualIsaacSnapshotAndWireSamplesMatchUnityRegistry',
        'AcousticVocab.Tests.ActualNeutralRendererTests.RecordedProtectedLiveFramesMatchSnapshotAndImportedRenderer',
        'AcousticVocab.Tests.StudyAudio.PackageLoaderTests.ActualProducerArtifactChecksAAndBWhenProvisioned',
        'AcousticVocab.Tests.SpeechBankTests.ActualPrivateSyntheticSpeechBankVerifiesButStaysUnreviewed'
    )
    $skippedTests = @($result.SelectNodes('//test-case[@result="Skipped"]'))
    $unexpectedSkip = @($skippedTests | Where-Object { $_.fullname -notin $optionalRecordedChecks })
    if ([int]$result.'test-run'.failed -ne 0 -or [int]$result.'test-run'.inconclusive -ne 0 -or
        [int]$result.'test-run'.passed -lt 1 -or $unexpectedSkip.Count -ne 0 -or $skippedTests.Count -gt $optionalRecordedChecks.Count) {
        throw 'Unity tests failed, were inconclusive, unexpectedly skipped, or discovered no passing tests.'
    }
    foreach ($skipped in $skippedTests) { Write-Output ('Optional recorded-Isaac evidence check skipped: '+$skipped.fullname) }
}
if ($Target -in @('Android','Windows')) {
    $binary = if ($Target -eq 'Android') { 'experiment.apk' } else { 'experiment.exe' }
    $buildDirectory = Join-Path $project "Builds/$BuildId/$Target"
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $buildDirectory ($binary+'.build.json')) | ConvertFrom-Json
    if ($manifest.result -ne 'Succeeded' -or $manifest.errors -ne 0 -or $manifest.build_identity.commit_sha -ne $revision -or $manifest.build_identity.build_id -ne $BuildId -or $manifest.files.Count -lt 1) { throw 'Build record, error count, or identity mismatch.' }
    foreach ($entry in $manifest.files) {
        $file = Join-Path $buildDirectory $entry.path
        if ((Get-Item -LiteralPath $file).Length -ne $entry.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant() -ne $entry.sha256) { throw 'Built file failed manifest verification.' }
    }
}
Write-Output "Foundation $Target completed. Identity=$BuildId; commit=$revision; dirty=$dirty"
