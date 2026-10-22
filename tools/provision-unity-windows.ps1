#requires -Version 7.4
[CmdletBinding()]
param(
    [string]$Unity,
    [string]$G1Description,
    [string]$OutputDirectory,
    [ValidateSet('manual_file','preactivated')][string]$LicenseMode = 'preactivated',
    [switch]$ProbeLicensedEditor,
    [switch]$IsolationConfirmed,
    [switch]$RegisterRunner,
    [string]$RunnerDirectory,
    [string]$RunnerArchive,
    [string]$RunnerArchiveSha256,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')][string]$RunnerName = 'unity-6000.6.0f1-x64',
    [ValidateRange(30,3600)][int]$ProbeTimeoutSeconds = 900
)
$ErrorActionPreference = 'Stop'

function Assert-RunnerLocalPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -match '[\x00-\x1f"]' -or
        $Path.StartsWith('\\') -or $Path.StartsWith('//') -or -not [IO.Path]::IsPathFullyQualified($Path)) {
        throw 'RUNNER_LOCAL_ABSOLUTE_PATH_REQUIRED'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($IsWindows -and [IO.DriveInfo]::new([IO.Path]::GetPathRoot($full)).DriveType -ne [IO.DriveType]::Fixed) {
        throw 'RUNNER_LOCAL_FIXED_DRIVE_REQUIRED'
    }
    for ($at = $full; $at; $at = [IO.Path]::GetDirectoryName($at)) {
        if (Test-Path -LiteralPath $at) {
            if ((Get-Item -Force -LiteralPath $at).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'RUNNER_REPARSE_PATH_REFUSED'
            }
        }
    }
    return $full
}

function Assert-RunnerHash([string]$Path, [string]$Sha256) {
    $full = Assert-RunnerLocalPath $Path
    if ($Sha256 -notmatch '^[0-9a-f]{64}$' -or -not (Test-Path -LiteralPath $full -PathType Leaf) -or
        (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
        throw 'RUNNER_FILE_HASH_MISMATCH'
    }
}

function Get-RunnerCachePath([string]$Root, [string]$Relative) {
    if ($Relative -notmatch '^meshes/[A-Za-z0-9_.-]+\.avmesh$') { throw 'RUNNER_MESH_PATH_REFUSED' }
    return Assert-RunnerLocalPath (Join-Path $Root $Relative)
}

function Test-RunnerG1Cache([string]$Description) {
    Assert-RunnerHash $Description '6ff5a6f555cb650e628fff674d28827df6f8fb4618afcb61506c0f868d1cbb41'
    $doc = Get-Content -LiteralPath $Description -Raw | ConvertFrom-Json
    if ($doc.schema_version -ne 1 -or $doc.source_sha256 -ne '97da67732d067c3147fc5fb7b7bafc8982718f4e7f8c92ff82266a4d9c07200d' -or $doc.meshes.Count -ne 49) {
        throw 'RUNNER_G1_DESCRIPTION_REFUSED'
    }
    $root = Split-Path $Description -Parent
    foreach ($mesh in $doc.meshes) { Assert-RunnerHash (Get-RunnerCachePath $root $mesh.file) $mesh.converted_sha256 }
    return @{ description_sha256='6ff5a6f555cb650e628fff674d28827df6f8fb4618afcb61506c0f868d1cbb41'; verified_meshes=49 }
}

function New-RunnerPrivateDirectory([string]$Path) {
    $full = Assert-RunnerLocalPath $Path
    if (Test-Path -LiteralPath $full) { throw 'RUNNER_FRESH_DIRECTORY_REQUIRED' }
    [void][IO.Directory]::CreateDirectory($full)
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
    }
    Set-Acl -LiteralPath $full -AclObject $acl
    return $full
}

function Test-RunnerFileSystem([string]$Root) {
    $dir = Join-Path $Root 'filesystem-probe'
    [void][IO.Directory]::CreateDirectory($dir)
    $file = Join-Path $dir 'source.bin'
    $first = [IO.FileStream]::new($file,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
    try {
        $first.WriteByte(42); $first.Flush($true)
        $hard = Join-Path $dir 'hard.bin'
        [void](New-Item -ItemType HardLink -Path $hard -Target $file)
        if ([IO.File]::ReadAllBytes($hard)[0] -ne 42) { throw 'RUNNER_HARDLINK_REFUSED' }
        $second = [IO.FileStream]::new($file,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
        try {
            $first.Lock(0,1); $blocked=$false
            try { $second.Lock(0,1); $second.Unlock(0,1) } catch [IO.IOException] { $blocked=$true }
            if (-not $blocked) { throw 'RUNNER_BYTE_LOCK_REFUSED' }
            $first.Unlock(0,1)
        } finally { $second.Dispose() }
        $target = Join-Path $dir 'target'; [void][IO.Directory]::CreateDirectory($target)
        $link = Join-Path $dir 'directory-link'
        [void](New-Item -ItemType SymbolicLink -Path $link -Target $target)
        if (-not ((Get-Item -Force -LiteralPath $link).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'RUNNER_DIRECTORY_LINK_REFUSED' }
        [IO.Directory]::Delete($link)
    } finally { $first.Dispose() }
    return @{ hardlink=$true; byte_lock=$true; durable_flush=$true; owned_directory_symlink=$true }
}

function Assert-RunnerProbeMarker($Marker,[string]$Nonce,[int]$ProcessId) {
    if ($null -eq $Marker) { throw 'RUNNER_LICENSE_PROBE_MARKER_REFUSED' }
    $names = @($Marker.PSObject.Properties.Name | Sort-Object)
    $expected = @('android_supported','editor_version','nonce','process_id' | Sort-Object)
    if ($names.Count -ne $expected.Count -or @(Compare-Object $names $expected).Count -ne 0 -or
        $Marker.nonce -isnot [string] -or $Marker.nonce -cne $Nonce -or
        ($Marker.process_id -isnot [long] -and $Marker.process_id -isnot [int]) -or $Marker.process_id -ne $ProcessId -or
        $Marker.editor_version -isnot [string] -or $Marker.editor_version -cne '6000.6.0f1' -or
        $Marker.android_supported -isnot [bool] -or -not $Marker.android_supported) {
        throw 'RUNNER_LICENSE_PROBE_MARKER_REFUSED'
    }
}

function Invoke-RunnerLicensedProbe([string]$Editor,[string]$Root,[int]$TimeoutSeconds) {
    $project = Join-Path $Root 'license-probe-project'
    [void][IO.Directory]::CreateDirectory((Join-Path $project 'Assets/Editor'))
    [void][IO.Directory]::CreateDirectory((Join-Path $project 'Packages'))
    [void][IO.Directory]::CreateDirectory((Join-Path $project 'ProjectSettings'))
    [IO.File]::WriteAllText((Join-Path $project 'Packages/manifest.json'),'{"dependencies":{"com.unity.modules.jsonserialize":"1.0.0"}}')
    [IO.File]::WriteAllText((Join-Path $project 'ProjectSettings/ProjectVersion.txt'),"m_EditorVersion: 6000.6.0f1`nm_EditorVersionWithRevision: 6000.6.0f1 (f7f8ed4d1e24)`n")
    $script = @'
using System;
using System.Diagnostics;
using System.IO;
using UnityEditor;
using UnityEngine;
public static class WindowsRunnerLicenseProbe {
  [Serializable] public sealed class Marker { public string nonce,editor_version; public int process_id; public bool android_supported; }
  public static void Run() {
    var value=new Marker { nonce=Environment.GetEnvironmentVariable("AV_RUNNER_PROBE_NONCE"),editor_version=Application.unityVersion,
      process_id=Process.GetCurrentProcess().Id,android_supported=BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Android,BuildTarget.Android) };
    if(value.editor_version!="6000.6.0f1"||!value.android_supported||string.IsNullOrEmpty(value.nonce)) throw new InvalidDataException("RUNNER_PROBE_PREREQUISITE");
    using(var file=new FileStream(Environment.GetEnvironmentVariable("AV_RUNNER_PROBE_MARKER"),FileMode.CreateNew,FileAccess.Write,FileShare.Read))
    using(var writer=new StreamWriter(file)) { writer.Write(JsonUtility.ToJson(value));writer.Flush();file.Flush(true); }
    EditorApplication.Exit(0);
  }
}
'@
    [IO.File]::WriteAllText((Join-Path $project 'Assets/Editor/WindowsRunnerLicenseProbe.cs'),$script)
    $nonce=[guid]::NewGuid().ToString('N'); $marker=Join-Path $Root 'license-probe-marker.local.json'
    $process = Start-Process -FilePath $Editor -ArgumentList @('-batchmode','-nographics','-projectPath',('"'+$project+'"'),'-executeMethod','WindowsRunnerLicenseProbe.Run','-logFile',('"'+(Join-Path $Root 'license-probe.private.log')+'"')) -Environment @{AV_RUNNER_PROBE_NONCE=$nonce;AV_RUNNER_PROBE_MARKER=$marker;ACTIONS_RUNNER_INPUT_TOKEN=$null;UNITY_LICENSE=$null} -RedirectStandardOutput (Join-Path $Root 'license-probe.stdout.log') -RedirectStandardError (Join-Path $Root 'license-probe.stderr.log') -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds*1000)) { $process.Kill(); throw 'RUNNER_LICENSE_PROBE_TIMEOUT' }
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'RUNNER_LICENSED_STARTUP_NOT_VERIFIED' }
    $value=Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    Assert-RunnerProbeMarker $value $nonce $process.Id
    return @{ completed=$true; editor_version=$value.editor_version; android_supported=$true; marker_sha256=(Get-FileHash -LiteralPath $marker -Algorithm SHA256).Hash.ToLowerInvariant(); process_exit=0 }
}

function Invoke-WindowsUnityProvisioning {
    if (-not $IsWindows) { throw 'RUNNER_NATIVE_WINDOWS_X64_REQUIRED' }
    if (-not ('UnityRunnerNativeMachine' -as [type])) { Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;
public static class UnityRunnerNativeMachine {
 [DllImport("kernel32.dll",SetLastError=true)] public static extern bool IsWow64Process2(IntPtr process,out ushort processMachine,out ushort nativeMachine);
}
'@
    }
    [ushort]$processMachine=0; [ushort]$nativeMachine=0
    if (-not [UnityRunnerNativeMachine]::IsWow64Process2([Diagnostics.Process]::GetCurrentProcess().Handle,[ref]$processMachine,[ref]$nativeMachine) -or $nativeMachine -ne 0x8664 -or -not [Environment]::Is64BitProcess) { throw 'RUNNER_NATIVE_WINDOWS_X64_REQUIRED' }
    if (-not $IsolationConfirmed) { throw 'RUNNER_ISOLATION_ACKNOWLEDGEMENT_REQUIRED' }
    $editor=Assert-RunnerLocalPath $Unity; $description=Assert-RunnerLocalPath $G1Description
    $out=Assert-RunnerLocalPath $OutputDirectory
    $repo=Assert-RunnerLocalPath (Split-Path $PSScriptRoot -Parent)
    if ($out -notmatch '(^|[\\/])(\.local|private|local-data)([\\/]|$)') { throw 'RUNNER_PRIVATE_OUTPUT_REQUIRED' }
    if (-not $out.StartsWith($repo.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'RUNNER_OUTPUT_MUST_BE_IN_CHECKOUT' }
    $out=New-RunnerPrivateDirectory $out
    $report=[ordered]@{ schema_version='unity-windows-preflight-1'; observed_at_utc=[DateTime]::UtcNow.ToString('o'); native_windows_x64=$true; isolation_acknowledged=$true; license_mode=$LicenseMode; local_preflight_passed=$false; github_ci_ready=$false; runner_registered=$false; checks=@{}; blocker=$null }
    try {
        if ((& git -C $repo status --porcelain).Length -ne 0 -or $LASTEXITCODE -ne 0) { throw 'RUNNER_SOURCE_MUST_BE_CLEAN' }
        $report.source_commit=(& git -C $repo rev-parse HEAD).Trim()
        $report.checks.git=(& git --version).Trim(); if ($LASTEXITCODE -ne 0) { throw 'RUNNER_GIT_REQUIRED' }
        $report.checks.git_lfs=(& git lfs version).Trim(); if ($LASTEXITCODE -ne 0) { throw 'RUNNER_GIT_LFS_REQUIRED' }
        $uv=(& uv --version).Trim(); if ($LASTEXITCODE -ne 0 -or $uv -notmatch '^uv 0\.11\.10(?:\s|$)') { throw 'RUNNER_PINNED_UV_REQUIRED' }; $report.checks.uv=$uv
        # File metadata is diagnostic only; the executed editor must report the exact pin.
        if (-not (Test-Path -LiteralPath $editor -PathType Leaf) -or [IO.Path]::GetFileName($editor) -ine 'Unity.exe') { throw 'RUNNER_PINNED_EDITOR_REQUIRED' }
        $android=Join-Path (Split-Path $editor -Parent) 'Data/PlaybackEngines/AndroidPlayer'
        foreach ($relative in @('UnityEditor.Android.Extensions.dll','SDK/platform-tools/adb.exe','NDK/source.properties','OpenJDK/bin/java.exe')) {
            if (-not (Test-Path -LiteralPath (Join-Path $android $relative) -PathType Leaf)) { throw 'RUNNER_ANDROID_TOOLS_REQUIRED' }
        }
        if (@(Get-ChildItem -Path (Join-Path $android 'SDK/build-tools/*/aapt2.exe') -File).Count -eq 0 -or @(Get-ChildItem -Path (Join-Path $android 'SDK/platforms/android-*/android.jar') -File).Count -eq 0) { throw 'RUNNER_ANDROID_SDK_REQUIRED' }
        $report.checks.android_modules_present=$true
        $report.checks.g1_cache=Test-RunnerG1Cache $description
        $report.checks.filesystem=Test-RunnerFileSystem $out
        if (-not $ProbeLicensedEditor) { throw 'RUNNER_LICENSED_STARTUP_PROBE_REQUIRED' }
        $report.checks.licensed_editor=Invoke-RunnerLicensedProbe $editor $out $ProbeTimeoutSeconds
        $report.local_preflight_passed=$true
        $report.repository_variables=@{UNITY_EDITOR_PATH=$editor;UNITY_G1_DESCRIPTION_PATH=$description;UNITY_LICENSE_MODE=$LicenseMode}
        if ($RegisterRunner) {
            $runner=Assert-RunnerLocalPath $RunnerDirectory
            if ($runner.Equals($repo,[StringComparison]::OrdinalIgnoreCase) -or $runner.StartsWith($repo.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'RUNNER_DIRECTORY_MUST_BE_OUTSIDE_CHECKOUT' }
            Assert-RunnerHash $RunnerArchive $RunnerArchiveSha256
            if ([IO.Path]::GetFileName($RunnerArchive) -notmatch '^actions-runner-win-x64-[0-9.]+\.zip$') { throw 'RUNNER_WIN_X64_ARCHIVE_REQUIRED' }
            if ([string]::IsNullOrWhiteSpace($env:ACTIONS_RUNNER_INPUT_TOKEN)) { throw 'RUNNER_SHORT_LIVED_REGISTRATION_TOKEN_REQUIRED' }
            $runner=New-RunnerPrivateDirectory $runner
            Expand-Archive -LiteralPath $RunnerArchive -DestinationPath $runner
            Get-ChildItem -LiteralPath $runner -File -Recurse | Unblock-File
            $listener=Join-Path $runner 'bin/Runner.Listener.exe'
            $registration=Start-Process -FilePath $listener -WorkingDirectory $runner -ArgumentList @('configure','--unattended','--url','https://github.com/ATR-Lab/acoustic-vocabularies','--name',$RunnerName,'--labels','unity-6000.6.0f1','--work','_work') -Environment @{ACTIONS_RUNNER_INPUT_TOKEN=$env:ACTIONS_RUNNER_INPUT_TOKEN;UNITY_LICENSE=$null} -RedirectStandardOutput (Join-Path $out 'registration.private.log') -RedirectStandardError (Join-Path $out 'registration.stderr.log') -WindowStyle Hidden -PassThru
            if (-not $registration.WaitForExit(120000)) { $registration.Kill(); throw 'RUNNER_REGISTRATION_TIMEOUT' }
            if ($registration.ExitCode -ne 0) { throw 'RUNNER_REGISTRATION_FAILED' }
            $registered=Get-Content -LiteralPath (Join-Path $runner '.runner') -Raw | ConvertFrom-Json
            if ($registered.gitHubUrl.TrimEnd('/') -cne 'https://github.com/ATR-Lab/acoustic-vocabularies') { throw 'RUNNER_REGISTERED_REPOSITORY_MISMATCH' }
            $report.runner_registered=$true
            $report.runner_start_command=Join-Path $runner 'run.cmd'
        }
    } catch {
        $report.blocker=if ($_.Exception.Message -match '^RUNNER_[A-Z0-9_]+$') { $_.Exception.Message } else { 'RUNNER_PREFLIGHT_FAILED_INSPECT_PRIVATE_LOGS' }
    } finally {
        if ($RegisterRunner) { Remove-Item Env:ACTIONS_RUNNER_INPUT_TOKEN -ErrorAction SilentlyContinue }
        $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $out 'readiness.local.json') -Encoding utf8NoBOM
    }
    if ($report.blocker) { throw $report.blocker }
    Write-Output 'Local Windows preflight passed. Start the registered runner under this same licensed account; GitHub online state and CI activation mode still require verification.'
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-WindowsUnityProvisioning }
