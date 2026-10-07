param([Parameter(Mandatory)][string]$Requests,[Parameter(Mandatory)][string]$Output)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$requestFile=(Resolve-Path -LiteralPath $Requests).Path
$plan=Get-Content -LiteralPath $requestFile -Raw | ConvertFrom-Json
$root=[IO.Path]::GetFullPath($Output)
if ($root -notmatch '[\\/]\.local[\\/]' -or (Test-Path -LiteralPath $root)) { throw 'A fresh private .local output directory is required' }
$walk=Split-Path $root -Parent
while ($walk) { if ((Test-Path -LiteralPath $walk) -and ((Get-Item -LiteralPath $walk).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Linked output path rejected' }; $walk=Split-Path $walk -Parent }
if ($plan.version -ne 1 -or $plan.status -ne 'engineering_unreviewed' -or $plan.voice.id -ne 'TTS_MS_EN-US_ZIRA_11.0' -or $plan.voice.name -ne 'Microsoft Zira Desktop' -or $plan.requests.Count -ne 64) { throw 'Unsupported speech plan' }
$synth=[System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice('Microsoft Zira Desktop')
    if ($synth.Voice.Id -ne $plan.voice.id -or $synth.Voice.Culture.Name -ne 'en-US' -or $synth.Voice.AdditionalInfo['Version'] -ne '11.0') { throw 'Installed voice pin mismatch' }
    $synth.Rate=0;$synth.Volume=100
    $format=[System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(48000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
    New-Item -ItemType Directory -Path $root | Out-Null
    $files=[ordered]@{}
    foreach ($item in $plan.requests) {
        if ($item.speech_id -notmatch '^speech-(add_one|remove_one|flip_card|align_arrow|scan|tag|close|quarantine)-[a-h]-t[12]$' -or $item.target -notmatch '^[A-H]$' -or $item.action -notmatch '^(ADD_ONE|REMOVE_ONE|FLIP_CARD|ALIGN_ARROW|SCAN|TAG|CLOSE|QUARANTINE)$') { throw 'Invalid speech request' }
        $destination=Join-Path $root ($item.speech_id+'.wav')
        if (Test-Path -LiteralPath $destination) { throw 'Duplicate speech request' }
        $prompt=[System.Speech.Synthesis.PromptBuilder]::new([Globalization.CultureInfo]::GetCultureInfo('en-US'))
        $prompt.AppendText($item.action.Replace('_',' ').ToLowerInvariant()+', ')
        # Spell the panel's one-letter target explicitly, avoiding an article reading of A.
        $prompt.AppendTextWithHint($item.target,[System.Speech.Synthesis.SayAs]::SpellOut)
        $prompt.AppendText('.')
        $synth.SetOutputToWaveFile($destination,$format)
        $synth.Speak($prompt)
        $synth.SetOutputToNull()
        $files[$item.speech_id+'.wav']=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $voiceKey=Get-Item -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_ZIRA_11.0'
    $pins=[ordered]@{}
    foreach ($field in @('VoicePath','LangDataPath')) {
        $prefix=[Environment]::ExpandEnvironmentVariables([string]$voiceKey.GetValue($field))
        if (-not $prefix) { throw 'Voice resource pin unavailable' }
        $parent=Split-Path $prefix -Parent;$stem=Split-Path $prefix -Leaf
        foreach ($resource in Get-ChildItem -LiteralPath $parent -File | Where-Object {$_.Name.StartsWith($stem,[StringComparison]::OrdinalIgnoreCase)}) {
            $pins[$resource.Name]=(Get-FileHash -LiteralPath $resource.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    if ($pins.Count -lt 1) { throw 'No voice resource pins found' }
    $engineKey=Get-Item -LiteralPath ('Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Classes\CLSID\'+$voiceKey.GetValue('CLSID')+'\InprocServer32')
    $engine=[Environment]::ExpandEnvironmentVariables([string]$engineKey.GetValue(''))
    $pins[[IO.Path]::GetFileName($engine)]=(Get-FileHash -LiteralPath $engine -Algorithm SHA256).Hash.ToLowerInvariant()
    $assembly=[System.Speech.Synthesis.SpeechSynthesizer].Assembly
    $evidence=[ordered]@{version=1;voice=$plan.voice;requests_sha256=(Get-FileHash -LiteralPath $requestFile -Algorithm SHA256).Hash.ToLowerInvariant();os_version=[Environment]::OSVersion.Version.ToString();speech_assembly_version=$assembly.GetName().Version.ToString();speech_assembly_sha256=(Get-FileHash -LiteralPath $assembly.Location -Algorithm SHA256).Hash.ToLowerInvariant();voice_resources=$pins;files=$files}
    $evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $root 'synthesis.local.json') -Encoding utf8
    Write-Output ('Synthesized '+$files.Count+' private speech WAVs; listening review remains required.')
} finally { $synth.Dispose() }
