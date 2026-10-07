param(
    [Parameter(Mandatory=$true)][string]$EditorPath,
    [ValidateSet('Configure','Windows','Android')][string]$Action = 'Configure',
    [string]$TemporaryDirectory,
    [string]$GradleCacheDirectory
)
$ErrorActionPreference = 'Stop'
$taskRepo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$taskProject = Join-Path $taskRepo 'unity/spikes/openxr'
$taskLogs = Join-Path $taskRepo '.local/unity'
New-Item -ItemType Directory -Force -Path $taskLogs | Out-Null
$taskMethod = @{Configure='Configure';Windows='BuildWindows';Android='BuildAndroid'}[$Action]
$taskArgs = @('-batchmode','-nographics','-projectPath',('"'+$taskProject+'"'),
    '-executeMethod',('AcousticVocab.Spikes.OpenXR.Editor.SpikeBuild.'+$taskMethod),
    '-quit','-logFile',('"'+(Join-Path $taskLogs ($Action+'.log'))+'"'))
if ($Action -eq 'Windows') { $taskArgs += @('-buildTarget','Win64') }
if ($Action -eq 'Android') { $taskArgs += @('-buildTarget','Android') }
$taskEnvironment = @{}
if ($TemporaryDirectory) {
    New-Item -ItemType Directory -Force -Path $TemporaryDirectory | Out-Null
    $taskTemp = (Resolve-Path -LiteralPath $TemporaryDirectory).Path
    $taskEnvironment.TEMP = $taskTemp
    $taskEnvironment.TMP = $taskTemp
}
if ($GradleCacheDirectory) {
    New-Item -ItemType Directory -Force -Path $GradleCacheDirectory | Out-Null
    $taskEnvironment.GRADLE_USER_HOME = (Resolve-Path -LiteralPath $GradleCacheDirectory).Path
}
$taskStartOptions = @{FilePath=$EditorPath;ArgumentList=$taskArgs;WindowStyle='Hidden';PassThru=$true;Wait=$true}
if ($taskEnvironment.Count) { $taskStartOptions.Environment = $taskEnvironment }
$taskUnity = Start-Process @taskStartOptions
if ($taskUnity.ExitCode -ne 0) { throw "Unity exited $($taskUnity.ExitCode); inspect ignored .local/unity/$Action.log" }
Write-Output "Unity $Action completed; inspect .local/unity/$Action.log for the OPENXR_SPIKE marker."
