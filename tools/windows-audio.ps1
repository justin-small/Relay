<#
Live Caption Relay on Windows: carries the host's microphones into the
container through a PulseAudio daemon running natively on Windows -- the same
shape as macOS. Works the same on Windows 10 and 11.

  windows-audio.ps1 install   download the pinned PulseAudio build (setup.bat)
  windows-audio.ps1 start     start the daemon if it is not already up (start.bat)
  windows-audio.ps1 stop      stop the daemon this script started (stop.bat)

The daemon listens on 127.0.0.1 only. Docker Desktop forwards the container's
host.docker.internal to the host's loopback, so the container reaches it while
nothing on the LAN can -- which matters, because the protocol is
unauthenticated and anything that can connect can listen to the microphone.
Binding loopback also means Windows Firewall never asks about it.

One source is loaded per Windows recording device, named after the device, so
the operator panel lists them the way it does on macOS. A device plugged in
later needs stop.bat + start.bat (or `stop` + `start` here) to appear.

Written for Windows PowerShell 5.1, which is what every Windows 10 has.
#>
param([Parameter(Mandatory)][ValidateSet('install', 'start', 'stop')][string]$Action)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Dir = Join-Path $Root '.pulseaudio'           # git-ignored, outside the image
$Exe = Join-Path $Dir 'pulseaudio\bin\pulseaudio.exe'
$Port = 4713
$Log = Join-Path $env:TEMP 'relay-pulseaudio.log'
$Script = Join-Path $env:TEMP 'relay-pulseaudio.pa'

# pgaskin/pulseaudio-win32 v5 (PulseAudio 15, MIT build scripts, LGPL build
# output). The release publishes no checksum, so this one is ours: computed
# when the build was first vetted. A mismatch means the asset changed under
# the same URL, and it is refused rather than run.
$Url = 'https://github.com/pgaskin/pulseaudio-win32/releases/download/v5/pulseaudio.zip'
$Sha256 = '13B54A7FD79E96B06BCABA4443BDAFD6A5093CF0354E9CF7FDCF765C6C7AA946'

function Test-Listening {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-OurDaemon {
    Get-Process pulseaudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Exe }
}

# Windows' legacy capture API, which module-waveout drives: device indices and
# the names it reports for them (truncated by Windows to 31 characters).
function Get-WaveInDevices {
    if (-not ('Relay.WaveIn' -as [type])) {
        Add-Type -Namespace Relay -Name WaveIn -MemberDefinition @'
[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential, CharSet = System.Runtime.InteropServices.CharSet.Ansi)]
public struct Caps {
    public ushort wMid; public ushort wPid; public uint vDriverVersion;
    [System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.ByValTStr, SizeConst = 32)]
    public string szPname;
    public uint dwFormats; public ushort wChannels; public ushort wReserved1;
}
[System.Runtime.InteropServices.DllImport("winmm.dll")]
public static extern uint waveInGetNumDevs();
[System.Runtime.InteropServices.DllImport("winmm.dll", CharSet = System.Runtime.InteropServices.CharSet.Ansi)]
public static extern uint waveInGetDevCapsA(System.UIntPtr id, ref Caps caps, uint size);
'@
    }
    $n = [Relay.WaveIn]::waveInGetNumDevs()
    for ($i = 0; $i -lt $n; $i++) {
        $c = New-Object Relay.WaveIn+Caps
        $size = [Runtime.InteropServices.Marshal]::SizeOf($c)
        if ([Relay.WaveIn]::waveInGetDevCapsA([UIntPtr][uint32]$i, [ref]$c, $size) -eq 0) {
            [pscustomobject]@{ Index = $i; Name = $c.szPname }
        }
    }
}

switch ($Action) {
    'install' {
        if (Test-Path $Exe) { Write-Host "PulseAudio - already installed"; exit 0 }
        Write-Host 'Downloading PulseAudio for Windows (9 MB)...'
        # Windows PowerShell 5.1 still defaults to TLS 1.0 on older Windows 10.
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $zip = Join-Path $env:TEMP 'relay-pulseaudio.zip'
        $ProgressPreference = 'SilentlyContinue'   # the progress bar slows iwr 10x
        Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing
        $got = (Get-FileHash $zip -Algorithm SHA256).Hash
        if ($got -ne $Sha256) {
            Remove-Item $zip -Force
            Write-Host "  Checksum mismatch - refusing to install."
            Write-Host "    expected $Sha256"
            Write-Host "    got      $got"
            exit 1
        }
        if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force }
        Expand-Archive $zip $Dir
        Remove-Item $zip -Force
        Write-Host "PulseAudio - ok ($Dir)"
    }

    'start' {
        if (-not (Test-Path $Exe)) {
            Write-Host '  PulseAudio is not installed. Run setup.bat first.'
            exit 1
        }
        if (Test-Listening) { Write-Host "PulseAudio already running on port $Port."; exit 0 }

        $devices = @(Get-WaveInDevices)
        if (-not $devices) {
            Write-Host '  Windows reports no recording devices. Plug in the microphone or'
            Write-Host '  interface, check it is enabled in Sound settings, then try again.'
            exit 1
        }
        # -n skips the build's default.pa, which would also open speakers and
        # load modules this needs none of. .nofail lets one device that is busy
        # or broken be skipped rather than take the whole daemon down.
        $lines = @('.nofail')
        foreach ($d in $devices) {
            $lines += "load-module module-waveout record=1 playback=0 input_device=$($d.Index) source_name=win_in_$($d.Index) rate=48000 channels=2"
        }
        $lines += '.fail'
        $lines += "load-module module-native-protocol-tcp port=$Port listen=127.0.0.1 auth-anonymous=1 auth-ip-acl=127.0.0.1"
        Set-Content -Path $Script -Value $lines -Encoding ASCII

        Write-Host "Starting PulseAudio on 127.0.0.1:$Port..."
        foreach ($d in $devices) { Write-Host "  input: $($d.Name)" }
        # Its own hidden process, so closing the start.bat window leaves it
        # running. The daemon writes its own log rather than having its output
        # redirected: -Redirect* makes Start-Process let the child inherit this
        # process's handles, and a daemon holding start.bat's stdout keeps any
        # caller that captures that output (a scheduled task, a remote shell)
        # waiting forever.
        Start-Process -FilePath $Exe -WindowStyle Hidden `
            -ArgumentList '-n', '--exit-idle-time=-1', "--log-target=`"file:$Log`"", '-F', "`"$Script`"" | Out-Null
        for ($i = 0; $i -lt 10 -and -not (Test-Listening); $i++) { Start-Sleep 1 }
        if (-not (Test-Listening)) {
            Write-Host "  PulseAudio did not come up. Log: $Log"
            Get-Content $Log -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { "    $_" }
            exit 1
        }
    }

    'stop' {
        # Only the daemon from .pulseaudio\ -- a PulseAudio run for anything
        # else is left alone.
        $p = Get-OurDaemon
        if ($p) { $p | Stop-Process -Force; Write-Host 'Stopped PulseAudio.' }
    }
}
exit 0
