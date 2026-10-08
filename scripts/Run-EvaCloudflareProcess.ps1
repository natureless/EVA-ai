param([Parameter(Mandatory = $true)][ValidateSet('App', 'Tunnel')][string]$Role)
$ErrorActionPreference = 'Stop'
$evaRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$evaPrivate = Join-Path $evaRoot '.cloudflare'
if ($Role -eq 'App') {
    $evaExecutable = (Get-Command python -ErrorAction Stop).Source
    $evaArguments = '-m app.cloudflare_server'
} else {
    $evaExecutable = Join-Path $evaRoot '.tools\cloudflared\2026.9.1\cloudflared.exe'
    $evaArguments = 'tunnel --no-autoupdate --metrics 127.0.0.1:20241 --loglevel warn run --token-file "{0}"' -f (Join-Path $evaPrivate 'tunnel-token')
}
# cloudflared can exit during initial edge discovery when DNS/network is not ready.
# Its reconnect logic does not cover that exit. Keep the Tunnel task alive so a
# transient failure cannot exhaust Task Scheduler's three outer restart attempts.
$evaRetryDelay = 5
$evaAttempt = 0
while ($true) {
    $evaAttempt++
    $evaLifetime = [System.Diagnostics.Stopwatch]::StartNew()
    $evaProcess = Start-Process -FilePath $evaExecutable -ArgumentList $evaArguments `
        -WorkingDirectory $evaRoot -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput (Join-Path $evaPrivate "$Role.stdout.log") `
        -RedirectStandardError (Join-Path $evaPrivate "$Role.stderr.log")
    $evaLifetime.Stop()
    if ($Role -eq 'App' -or $evaProcess.ExitCode -eq 0) { exit $evaProcess.ExitCode }

    if ($evaLifetime.Elapsed.TotalSeconds -ge 120) { $evaRetryDelay = 5 }
    # Only the latest failure is retained, in the existing private directory.
    foreach ($evaStream in @('stdout', 'stderr')) {
        $evaLog = Join-Path $evaPrivate "Tunnel.$evaStream.log"
        if (Test-Path -LiteralPath $evaLog -PathType Leaf) {
            Copy-Item -LiteralPath $evaLog -Destination (Join-Path $evaPrivate "Tunnel.$evaStream.previous.log") -Force
        }
    }
    [ordered]@{
        failed_at_utc = [DateTime]::UtcNow.ToString('o')
        attempt = $evaAttempt
        exit_code = $evaProcess.ExitCode
        retry_delay_seconds = $evaRetryDelay
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evaPrivate 'Tunnel.retry.json') -Encoding UTF8
    Start-Sleep -Seconds $evaRetryDelay
    $evaRetryDelay = [Math]::Min(60, $evaRetryDelay * 2)
}
