param([ValidateSet('Start', 'Status', 'Stop')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$evaRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$evaPrivate = Join-Path $evaRoot '.cloudflare'
$evaPython = (Get-Command python -ErrorAction Stop).Source
$evaCloudflared = Join-Path $evaRoot '.tools\cloudflared\2026.9.1\cloudflared.exe'
$evaToken = Join-Path $evaPrivate 'tunnel-token'
$evaTasks = @('EVA Cloudflare App', 'EVA Cloudflare Tunnel')

if ($Action -eq 'Status') {
    foreach ($evaTaskName in $evaTasks) {
        Get-ScheduledTask -TaskName $evaTaskName -ErrorAction SilentlyContinue |
            Select-Object TaskName, State
    }
    exit 0
}
if ($Action -eq 'Stop') {
    # Stop the public connector first. This does not remove DNS or Access policy.
    foreach ($evaTaskName in @('EVA Cloudflare Tunnel', 'EVA Cloudflare App')) {
        Stop-ScheduledTask -TaskName $evaTaskName -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName $evaTaskName -ErrorAction SilentlyContinue | Out-Null
    }
    Write-Output 'EVA Cloudflare tasks stopped and logon startup disabled.'
    exit 0
}

Push-Location -LiteralPath $evaRoot
try {
    & $evaPython -m app.cloudflare_server --check
    if ($LASTEXITCODE -ne 0) { throw 'Deployment configuration validation failed.' }
    if (-not (Test-Path -LiteralPath $evaToken -PathType Leaf)) { throw 'Missing protected tunnel-token file.' }
    $evaExpected = '2837888cc0f5d58f15b6dc478376de90b4d3ba5241c7947455d1e0a0df429712'
    if ((Get-FileHash -LiteralPath $evaCloudflared -Algorithm SHA256).Hash.ToLowerInvariant() -ne $evaExpected) {
        throw 'cloudflared binary checksum mismatch.'
    }
    $evaUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $evaTrigger = New-ScheduledTaskTrigger -AtLogOn -User $evaUser
    $evaPrincipal = New-ScheduledTaskPrincipal -UserId $evaUser -LogonType Interactive -RunLevel Limited
    $evaSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $evaPowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $evaRunner = Join-Path $PSScriptRoot 'Run-EvaCloudflareProcess.ps1'
    $evaActions = @(
        (New-ScheduledTaskAction -Execute $evaPowerShell -WorkingDirectory $evaRoot `
            -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Role App' -f $evaRunner)),
        (New-ScheduledTaskAction -Execute $evaPowerShell -WorkingDirectory $evaRoot `
            -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Role Tunnel' -f $evaRunner))
    )
    for ($evaIndex = 0; $evaIndex -lt $evaTasks.Count; $evaIndex++) {
        $evaExisting = Get-ScheduledTask -TaskName $evaTasks[$evaIndex] -ErrorAction SilentlyContinue
        if ($evaExisting) {
            if ($evaExisting.Actions.Execute -ne $evaActions[$evaIndex].Execute -or
                $evaExisting.Actions.Arguments -ne $evaActions[$evaIndex].Arguments) {
                throw "An existing task has different settings: $($evaTasks[$evaIndex]). Review it before replacing."
            }
            Enable-ScheduledTask -TaskName $evaTasks[$evaIndex] | Out-Null
        } else {
            Register-ScheduledTask -TaskName $evaTasks[$evaIndex] -Action $evaActions[$evaIndex] `
                -Trigger $evaTrigger -Principal $evaPrincipal -Settings $evaSettings | Out-Null
        }
        Start-ScheduledTask -TaskName $evaTasks[$evaIndex]
    }
    Write-Output 'Started EVA and named Tunnel; Tunnel retries abnormal exits with 5-60 second backoff. Tasks start at user logon; Task Scheduler retries runner failures three times.'
} finally {
    Pop-Location
}
