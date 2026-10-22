# Licensed Windows CI runner

The Unity foundation workflow needs an isolated **native Windows x64** machine,
Unity **6000.6.0f1 (f7f8ed4d1e24)** with Android support, and a license that works
under the exact account running GitHub Actions. ARM Windows with x64 emulation
cannot satisfy this gate. Hosted fixture or provisioning tests establish software
contracts only; the licensed self-hosted job must also pass.

## Prepare the account and tools

Use a dedicated account and a short, account-private checkout such as `C:\av\repo`
on a local fixed drive. Keep its runner directory separate, for example
`C:\av-runner`. Do not use OneDrive, network drives, UNC paths, directory junctions,
or redirected profiles. Install PowerShell **7.4+**, Git with Git LFS, and
**uv 0.11.10**. Clone the full repository and set `git config core.longpaths true`.
The runner account must be able to create directory symlinks; configure that
privilege on the isolated machine before proceeding.

Install the pinned editor through Unity Hub, including Android Build Support,
Android SDK/NDK Tools and OpenJDK. The prior reviewed station used SDK tools 36.0.0, NDK r27c
(27.2.12479018), and OpenJDK 17.0.18+8; these are recorded reference versions,
not evidence of tools installed on a new host. The preflight checks Android
module/tool presence and actual editor target support; the Android build remains
the toolchain integration check.

Activate the editor legitimately under the runner account. Unity Personal uses
Hub activation and does not support manual activation. Choose `preactivated`
for an already active Hub/server entitlement. Choose `manual_file` only for a
license that supports the workflow's existing manual-file activation. The script
does not request, read, copy, export, purchase, or activate a license.
[Unity activation requirements](https://docs.unity3d.com/6000.0/Documentation/Manual/ManualActivationGuide.html)
and [PowerShell environment support](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process?view=powershell-7.4#-environment).

## Reproduce the pinned G1 cache

Run from the clean checkout. These existing producer tools fetch only the pinned
BSD-3 Unitree meshes and deterministically convert them; no private study assets
are required. Each native command must exit successfully before continuing.

```powershell
uv sync --project banks --locked
uv run --project banks --locked python spikes/O5.1.4/fetch_meshes.py --output .local/unitree-g1
uv run --project banks --locked python spikes/O5.1.4/convert_urdf.py unity/Assets/ThirdParty/Unitree/G1/g1_29dof_with_hand_rev_1_0.urdf --mesh-root .local/unitree-g1 --output .local/converted-g1
```

The expected description SHA-256 is
`6ff5a6f555cb650e628fff674d28827df6f8fb4618afcb61506c0f868d1cbb41`.
The preflight also verifies all 49 converted mesh hashes and the pinned source
URDF hash. The [G1 runbook](../spikes/O5.1.4-runbook.md) records the existing
conversion contract. Leave generated caches ignored and local.

## Verify licensed execution before registration

Use a fresh output directory beneath this checkout on every attempt. Run this
as the account that will run Actions, after activation in either mode:

```powershell
$editor = 'C:\Program Files\Unity\Hub\Editor\6000.6.0f1\Editor\Unity.exe'
$g1 = Join-Path $PWD '.local/converted-g1/g1_description.json'
./tools/provision-unity-windows.ps1 -Unity $editor -G1Description $g1 -OutputDirectory (Join-Path $PWD '.local/windows-preflight-001') -LicenseMode preactivated -IsolationConfirmed -ProbeLicensedEditor
```

`-IsolationConfirmed` acknowledges that this is a dedicated account/workspace;
it does not make a shared machine isolated. The script refuses another OS or
native architecture, dirty source, reparse/network paths, missing tools, altered
G1 assets, failed hard links/byte locks/durable flushes, or missing directory
symlink privilege. Its fresh output has a protected ACL allowing the current
account and LocalSystem only.

The script opens a fresh minimal project and executes an editor method. A valid
result needs the exact editor version, Android target support, exit code zero,
and a fresh nonce tied to that editor process ID. A version string or old log
cannot substitute for this execution. Both activation modes require this probe.
The method explicitly exits; the wrapper waits for the editor process rather
than persistent licensing/helper descendants.
[Unity command-line execution](https://docs.unity3d.com/6000.0/Documentation/Manual/EditorCommandLineArguments.html).

Inspect the private `readiness.local.json`. A successful local preflight sets
`local_preflight_passed=true`; `github_ci_ready` remains false. Keep this report,
the generated project, and raw logs local. Do not upload licensing logs.

## Register and run under the same account

Get the official **win-x64** runner archive and its SHA-256 from GitHub's
repository **Settings → Actions → Runners → New self-hosted runner** instructions.
Use the exact downloaded archive and digest; do not invent or reuse a checksum.
Registration tokens expire after one hour. Obtain the short-lived token through
an already authorized administrator session without echoing it, and place it in
`ACTIONS_RUNNER_INPUT_TOKEN` in this PowerShell session. Never place a PAT or
registration token in a command argument, checked-in file, transcript, or log.

Repeat the successful preflight with a fresh output path and add:

```powershell
-RegisterRunner -RunnerDirectory 'C:\av-runner' -RunnerArchive '<local official actions-runner-win-x64-VERSION.zip>' -RunnerArchiveSha256 '<official 64-character SHA-256>'
```

The script verifies the archive hash before extraction, creates a private fresh
runner directory outside the checkout, and registers only with
`ATR-Lab/acoustic-vocabularies`, adding label `unity-6000.6.0f1`. The token is
passed only in the runner child's environment and cleared from the caller in
the registration cleanup path. License contents are excluded from both child
environments. The default GitHub Windows/X64 labels are not overridden.

Start the returned `run.cmd` from the **same licensed account**. Do not install
it under another service identity and assume that identity inherits activation.
Changing to a service requires separate activation and a fresh successful probe
under the real service identity. Confirm GitHub shows the runner online with
`self-hosted`, `Windows`, `X64`, and `unity-6000.6.0f1` labels.
[GitHub registration and token lifetime](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners).

After confirming the real runner is online, the repository administrator sets
`UNITY_EDITOR_PATH`, `UNITY_G1_DESCRIPTION_PATH`, and `UNITY_LICENSE_MODE` to the
verified values. Only `manual_file` requires secret `UNITY_LICENSE`. Set
`UNITY_FOUNDATION_RUNNER_READY=true` after this host and account verification.
The script reports suggested paths but never changes GitHub settings or READY.
The workflow must run the same preflight unconditionally after any manual
activation and before builds. The existing trusted-source and strict test-skip
checks remain mandatory.

## Regression checks without a licensed editor

```powershell
pwsh -NoLogo -NoProfile -File tests/powershell/test-provision-unity-windows.ps1
pwsh -NoLogo -NoProfile -File tests/powershell/test-provision-unity-windows.ps1 -WindowsFileSystem
```

The first command checks marker replay/type/version failures, hash refusal,
unsafe cache paths, and non-Windows rejection where applicable. The second must
run on actual Windows and additionally exercises protected ACLs, hard links,
conflicting byte locks, durable file flushes, owned directory symlinks, and
reparse-ancestor rejection. These checks never launch Unity or register a runner.
They cannot establish licensed readiness, Android build success, headset timing,
or study qualification.
