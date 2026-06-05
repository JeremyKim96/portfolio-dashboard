# Cloudflare Tunnel 실행 래퍼.
# cloudflared.exe를 stderr → tunnel.log 리다이렉트하여 백그라운드로 띄우고,
# 자식 프로세스가 종료될 때까지 본 PS도 살아있게 한다 (작업 스케줄러가 RUN 상태 유지).
#
# 작업 스케줄러 등록:
#   Execute : powershell.exe
#   Args    : -NoProfile -ExecutionPolicy Bypass -File "C:\...\run_tunnel.ps1"

$ErrorActionPreference = 'Stop'

$proj  = Split-Path -Parent $MyInvocation.MyCommand.Path
$cf    = Join-Path $proj 'tools\cloudflared.exe'
$log   = Join-Path $proj 'tunnel.log'

if (-not (Test-Path $cf)) { exit 1 }

# 이전 로그 비우기 (URL 깨끗하게 새로 발급되도록)
if (Test-Path $log) { Clear-Content $log -Force }

$p = Start-Process -FilePath $cf `
    -ArgumentList @('tunnel', '--url', 'http://localhost:8765', '--no-autoupdate') `
    -RedirectStandardError $log `
    -WindowStyle Hidden `
    -PassThru

# 작업 스케줄러가 작업 종료로 보지 않게 자식 종료까지 대기
$p.WaitForExit()
exit $p.ExitCode
