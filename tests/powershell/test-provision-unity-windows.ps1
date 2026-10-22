#requires -Version 7.4
[CmdletBinding()]
param([switch]$WindowsFileSystem)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $repo 'tools/provision-unity-windows.ps1')
$script:passed = 0

function Accept([scriptblock]$Body) { & $Body; $script:passed++ }
function Refuse([scriptblock]$Body, [string]$Code) {
    try { & $Body } catch {
        if ($_.Exception.Message -eq $Code) { $script:passed++; return }
        throw
    }
    throw "Expected refusal: $Code"
}
function Marker { return [pscustomobject]@{ android_supported=$true; editor_version='6000.6.0f1'; nonce='fresh-nonce'; process_id=12345 } }

$root = Join-Path $repo ('.local/provisioning-tests-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($root)
try {
    Accept { Assert-RunnerProbeMarker (Marker) 'fresh-nonce' 12345 }
    Refuse { Assert-RunnerProbeMarker $null 'fresh-nonce' 12345 } 'RUNNER_LICENSE_PROBE_MARKER_REFUSED'
    Refuse { Assert-RunnerProbeMarker (Marker) 'stale-nonce' 12345 } 'RUNNER_LICENSE_PROBE_MARKER_REFUSED'
    Refuse { Assert-RunnerProbeMarker (Marker) 'fresh-nonce' 12346 } 'RUNNER_LICENSE_PROBE_MARKER_REFUSED'
    foreach ($change in @(
        @{ field='editor_version'; value='6000.5.0f1' },
        @{ field='editor_version'; value=@('6000.6.0f1') },
        @{ field='android_supported'; value=$false },
        @{ field='android_supported'; value='true' },
        @{ field='process_id'; value='12345' },
        @{ field='process_id'; value=$true },
        @{ field='nonce'; value=@('fresh-nonce') }
    )) {
        $bad = Marker; $bad.($change.field) = $change.value
        Refuse { Assert-RunnerProbeMarker $bad 'fresh-nonce' 12345 } 'RUNNER_LICENSE_PROBE_MARKER_REFUSED'
    }
    $bad = Marker; $bad | Add-Member unexpected 'untrusted'
    Refuse { Assert-RunnerProbeMarker $bad 'fresh-nonce' 12345 } 'RUNNER_LICENSE_PROBE_MARKER_REFUSED'
    $bad = Marker; $bad.PSObject.Properties.Remove('nonce')
    Refuse { Assert-RunnerProbeMarker $bad 'fresh-nonce' 12345 } 'RUNNER_LICENSE_PROBE_MARKER_REFUSED'

    $file = Join-Path $root 'archive.bin'; [IO.File]::WriteAllText($file,'owned test bytes')
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    Accept { Assert-RunnerHash $file $hash }
    Refuse { Assert-RunnerHash $file ('0'*64) } 'RUNNER_FILE_HASH_MISMATCH'
    Refuse { Assert-RunnerHash $file 'invalid' } 'RUNNER_FILE_HASH_MISMATCH'
    Refuse { Assert-RunnerHash (Join-Path $root 'missing.bin') $hash } 'RUNNER_FILE_HASH_MISMATCH'
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) { throw 'Hash refusal modified input' }
    $script:passed++
    foreach ($invalid in @('relative/path','\\host\share\cache','//host/share/cache','\\?\C:\cache',("bad`npath"))) {
        Refuse { Assert-RunnerLocalPath $invalid } 'RUNNER_LOCAL_ABSOLUTE_PATH_REQUIRED'
    }
    Accept { [void](Get-RunnerCachePath $root 'meshes/pelvis.avmesh') }
    foreach ($invalid in @('../outside.avmesh','meshes/../outside.avmesh','meshes/nested/x.avmesh','meshes/x.stl','meshes\x.avmesh','/meshes/x.avmesh')) {
        Refuse { Get-RunnerCachePath $root $invalid } 'RUNNER_MESH_PATH_REFUSED'
    }
    Refuse { Test-RunnerG1Cache $file } 'RUNNER_FILE_HASH_MISMATCH'

    if (-not $IsWindows) {
        # Merely setting Windows-like environment values cannot qualify another OS.
        $before = @(Get-ChildItem -LiteralPath $root).Count
        Refuse { Invoke-WindowsUnityProvisioning } 'RUNNER_NATIVE_WINDOWS_X64_REQUIRED'
        if (@(Get-ChildItem -LiteralPath $root).Count -ne $before) { throw 'Non-Windows refusal produced output' }
        $script:passed++
        $target = Join-Path $root 'target'; [void][IO.Directory]::CreateDirectory($target)
        $link = Join-Path $root 'link'; [void](New-Item -ItemType SymbolicLink -Path $link -Target $target)
        try {
            Refuse { Assert-RunnerLocalPath (Join-Path $link 'missing') } 'RUNNER_REPARSE_PATH_REFUSED'
        } finally { [IO.Directory]::Delete($link) }
    }
    if ($WindowsFileSystem) {
        if (-not $IsWindows) { throw 'WindowsFileSystem requires actual Windows' }
        $private = New-RunnerPrivateDirectory (Join-Path $root 'private')
        Accept {
            $acl = Get-Acl -LiteralPath $private
            if (-not $acl.AreAccessRulesProtected) { throw 'Private ACL inherited access' }
            $allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18')
            foreach ($rule in $acl.Access) {
                if ($rule.AccessControlType -ne 'Allow' -or $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'Private ACL allowed another identity' }
            }
            if ($acl.Access.Count -lt 1) { throw 'Private ACL was empty' }
        }
        Accept {
            $result = Test-RunnerFileSystem $private
            foreach ($key in @('hardlink','byte_lock','durable_flush','owned_directory_symlink')) {
                if ($result[$key] -ne $true) { throw "Filesystem requirement failed: $key" }
            }
        }
        Refuse { New-RunnerPrivateDirectory $private } 'RUNNER_FRESH_DIRECTORY_REQUIRED'
        $link = Join-Path $private 'reparse-fixture'
        [void](New-Item -ItemType SymbolicLink -Path $link -Target (Join-Path $private 'filesystem-probe/target'))
        try {
            Refuse { Assert-RunnerLocalPath (Join-Path $link 'not-yet-created') } 'RUNNER_REPARSE_PATH_REFUSED'
        } finally { [IO.Directory]::Delete($link) }
    }
    Write-Output "Windows provisioning guard tests passed: $script:passed. Native Windows filesystem exercised: $WindowsFileSystem. No licensed editor or GitHub readiness claim."
} finally {
    # Only this fresh generated test directory is removed.
    Remove-Item -LiteralPath $root -Recurse -Force
}
