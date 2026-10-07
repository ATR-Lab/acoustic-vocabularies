param([Parameter(Mandatory=$true)][string]$UnityEditor)
$ErrorActionPreference = 'Stop'
$merge = Join-Path (Split-Path -Parent $UnityEditor) 'Data/Tools/UnityYAMLMerge.exe'
if (-not (Test-Path -LiteralPath $merge)) { throw 'UnityYAMLMerge not found next to selected editor' }
$merge = $merge.Replace('\','/')
git config --local merge.unityyamlmerge.name 'Unity Smart Merge'
git config --local merge.unityyamlmerge.driver ('"' + $merge + '" merge -p %O %B %A %A')
git config --local merge.unityyamlmerge.recursive binary
if ($LASTEXITCODE -ne 0) { throw 'git config failed' }
Write-Output 'Unity Smart Merge configured for this checkout. Resolve remaining conflicts in the editor.'
