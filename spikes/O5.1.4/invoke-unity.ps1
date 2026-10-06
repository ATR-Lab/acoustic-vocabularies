param(
    [Parameter(Mandatory=$true)][string]$EditorPath,
    [ValidateSet('Import','ExportPoses','BuildAndroidRobot','CaptureComparisonImages')][string]$Action='Import',
    [string]$TemporaryDirectory,
    [string]$GradleCacheDirectory
)
$ErrorActionPreference='Stop'
$taskRepo=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$taskProject=Join-Path $taskRepo 'unity/spikes/openxr'
$taskLogs=Join-Path $taskRepo '.local/unity'
New-Item -ItemType Directory -Force -Path $taskLogs | Out-Null
$taskArguments=@('-batchmode','-projectPath',('"'+$taskProject+'"'),'-executeMethod',
    ('AcousticVocab.Spikes.Urdf.Editor.UrdfSpikeImport.'+$Action),'-quit','-logFile',
    ('"'+(Join-Path $taskLogs ('Urdf-'+$Action+'.log'))+'"'))
if($Action -ne 'CaptureComparisonImages'){$taskArguments+='-nographics'}
if($Action -eq 'BuildAndroidRobot'){$taskArguments+=@('-buildTarget','Android')}
$taskEnvironment=@{}
if($TemporaryDirectory){New-Item -ItemType Directory -Force -Path $TemporaryDirectory | Out-Null;$taskEnvironment.TEMP=(Resolve-Path $TemporaryDirectory).Path;$taskEnvironment.TMP=$taskEnvironment.TEMP}
if($GradleCacheDirectory){New-Item -ItemType Directory -Force -Path $GradleCacheDirectory | Out-Null;$taskEnvironment.GRADLE_USER_HOME=(Resolve-Path $GradleCacheDirectory).Path}
$taskOptions=@{FilePath=$EditorPath;ArgumentList=$taskArguments;WindowStyle='Hidden';PassThru=$true;Wait=$true}
if($taskEnvironment.Count){$taskOptions.Environment=$taskEnvironment}
$taskProcess=Start-Process @taskOptions
if($taskProcess.ExitCode -ne 0){throw "Unity URDF $Action failed with exit $($taskProcess.ExitCode); see ignored log"}
Write-Output "Unity URDF $Action completed; check the URDF_SPIKE log marker."
