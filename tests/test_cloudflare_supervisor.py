"""Exercise the Windows task runner without launching EVA or cloudflared."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


POWERSHELL = shutil.which("powershell.exe")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell required")


def run_fake_connector(tmp_path, role, failures, failure_code=1):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    private = tmp_path / ".cloudflare"
    private.mkdir()
    runner = scripts / "Run-EvaCloudflareProcess.ps1"
    shutil.copyfile(ROOT / "scripts/Run-EvaCloudflareProcess.ps1", runner)
    wrapper = scripts / "probe.ps1"
    # Only fixed test values enter the PowerShell source; paths are passed as arguments.
    wrapper.write_text(
        """param([string]$Runner, [string]$Role, [int]$Failures, [int]$FailureCode)
$script:evaLaunches = 0
function Get-Command { param($Name, $ErrorAction); @{ Source = 'fake-python' } }
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle,
          [switch]$Wait, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
    if ($WindowStyle -ne 'Hidden' -or -not $Wait -or -not $PassThru) { throw 'invalid launch' }
    $script:evaLaunches++
    Write-Host "launch:$script:evaLaunches"
    Set-Content -LiteralPath $RedirectStandardOutput -Value 'simulated output'
    Set-Content -LiteralPath $RedirectStandardError -Value "simulated failure $script:evaLaunches"
    if ($script:evaLaunches -le $Failures) { return @{ ExitCode = $FailureCode } }
    return @{ ExitCode = 0 }
}
function Start-Sleep { param([int]$Seconds); Write-Host "sleep:$Seconds" }
& $Runner -Role $Role
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(wrapper), str(runner), role,
         str(failures), str(failure_code)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    return result, private


def test_tunnel_retries_beyond_outer_task_limit_and_caps_backoff(tmp_path):
    result, private = run_fake_connector(tmp_path, "Tunnel", 7)
    assert result.returncode == 0, result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("sleep:")] == [
        "sleep:5", "sleep:10", "sleep:20", "sleep:40", "sleep:60", "sleep:60", "sleep:60"
    ]
    assert "launch:8" in result.stdout
    retry = json.loads((private / "Tunnel.retry.json").read_text(encoding="utf-8-sig"))
    assert retry["attempt"] == 7
    assert retry["exit_code"] == 1
    assert retry["retry_delay_seconds"] == 60
    assert (private / "Tunnel.stderr.previous.log").read_text().strip() == "simulated failure 7"
    assert (private / "Tunnel.stderr.log").read_text().strip() == "simulated failure 8"


def test_tunnel_clean_exit_does_not_restart(tmp_path):
    result, private = run_fake_connector(tmp_path, "Tunnel", 0)
    assert result.returncode == 0, result.stderr
    assert "launch:1" in result.stdout
    assert "sleep:" not in result.stdout
    assert not (private / "Tunnel.retry.json").exists()


def test_app_failure_still_returns_to_task_scheduler(tmp_path):
    result, private = run_fake_connector(tmp_path, "App", 1, failure_code=7)
    assert result.returncode == 7, result.stderr
    assert "launch:1" in result.stdout
    assert "sleep:" not in result.stdout
    assert not (private / "Tunnel.retry.json").exists()
