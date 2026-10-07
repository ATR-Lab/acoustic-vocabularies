param(
    [Parameter(Mandatory)][string]$Adb,
    [Parameter(Mandatory)][string]$Serial,
    [Parameter(Mandatory)][string]$Apk,
    [Parameter(Mandatory)][string]$StationConfig,
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$Record
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
if ($Serial -notmatch '^[A-Za-z0-9._:-]+$') { throw 'Device identifier format invalid' }
if ([IO.Path]::GetFileName($StationConfig) -notlike '*.local.json') { throw 'Use an ignored private *.local.json configuration file.' }
$recordPath = [IO.Path]::GetFullPath($Record)
$privateRoot = [IO.Path]::GetFullPath((Join-Path $repo '.local')) + [IO.Path]::DirectorySeparatorChar
if (-not $recordPath.StartsWith($privateRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Deployment record must stay inside this checkout .local directory.' }
if (Test-Path -LiteralPath $recordPath) { throw 'Refusing to overwrite a deployment record.' }
$build = Get-Content -Raw -LiteralPath ($Apk+'.build.json') | ConvertFrom-Json
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Apk).Hash.ToLowerInvariant()
$entry = $build.files | Where-Object { $_.path -eq [IO.Path]::GetFileName($Apk) }
if ($build.result -ne 'Succeeded' -or $entry.sha256 -ne $actualHash -or $build.development_build) { throw 'APK manifest mismatch or development build.' }
& $Python (Join-Path $PSScriptRoot 'validate_station.py') $StationConfig --protocol $build.build_identity.protocol_version --topology standalone_quest
if ($LASTEXITCODE -ne 0) { throw 'Private configuration failed validation.' }
function Invoke-Device([string[]]$DeviceArguments) {
    $output = & $Adb -s $Serial @DeviceArguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'ADB operation failed; check the explicitly selected device locally.' }
    return ($output -join "`n")
}
if ((Invoke-Device -DeviceArguments @('get-state')).Trim() -ne 'device') { throw 'The named device is not authorized and connected.' }
$package = 'org.acousticvocab.experiment'
Invoke-Device -DeviceArguments @('install','-r',$Apk) | Out-Null
# Launch once to create app storage. Missing configuration keeps its view neutral.
Invoke-Device -DeviceArguments @('shell','monkey','-p',$package,'-c','android.intent.category.LAUNCHER','1') | Out-Null
Invoke-Device -DeviceArguments @('shell','am','force-stop',$package) | Out-Null
$destination = "/sdcard/Android/data/$package/files"
Invoke-Device -DeviceArguments @('shell','mkdir','-p',$destination) | Out-Null
Invoke-Device -DeviceArguments @('push',$StationConfig,"$destination/station.local.json") | Out-Null
Invoke-Device -DeviceArguments @('shell','monkey','-p',$package,'-c','android.intent.category.LAUNCHER','1') | Out-Null
$config = Get-Content -Raw -LiteralPath $StationConfig | ConvertFrom-Json
$recordValue = [ordered]@{ schema_version=1; event='engineering_deployment'; utc=[DateTime]::UtcNow.ToString('o'); station_id=$config.station_id; headset_unit_id=$config.headset_unit_id; build_identity=$build.build_identity; apk_sha256=$actualHash; config_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $StationConfig).Hash.ToLowerInvariant(); result='installed_and_launched'; human_view_check='pending'; os_settings_changed=$false }
New-Item -ItemType Directory -Path (Split-Path $recordPath -Parent) -Force | Out-Null
$recordValue | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $recordPath -Encoding utf8
Write-Output 'Installed and launched on the explicitly named device. Human view/comfort validation remains pending.'
