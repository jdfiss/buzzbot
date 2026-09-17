# 課堂錄音助理：一次啟動 Local Bot API Server + Python 助理
# 用量低（一週用個一兩次）不需要開機自動啟動，雙擊這支腳本、用完關掉視窗即可。

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "找不到 .env，請先複製 .env.example 成 .env 並填好設定。" -ForegroundColor Red
    exit 1
}
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $key = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim()
    Set-Item -Path "Env:$key" -Value $value
}

foreach ($required in @("BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH")) {
    $val = (Get-Item "Env:$required" -ErrorAction SilentlyContinue).Value
    if ([string]::IsNullOrWhiteSpace($val)) {
        Write-Host "缺少 $required，請先在 .env 補上再跑一次。" -ForegroundColor Red
        exit 1
    }
}

$botApiExe = Join-Path $PSScriptRoot "bot-api-server\telegram-bot-api.exe"
$botApiDataDir = Join-Path $PSScriptRoot "bot-api-server\data"
$logFile = Join-Path $botApiDataDir "server.log"
New-Item -ItemType Directory -Force -Path $botApiDataDir | Out-Null

Write-Host "啟動 Local Bot API Server..." -ForegroundColor Cyan
$argStr = "--local --api-id=$($env:TELEGRAM_API_ID) --api-hash=$($env:TELEGRAM_API_HASH) --http-port=8081 --dir=`"$botApiDataDir`" --log=`"$logFile`""
$serverProc = Start-Process -FilePath $botApiExe -ArgumentList $argStr -WindowStyle Hidden -PassThru

try {
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        $test = Test-NetConnection -ComputerName localhost -Port 8081 -WarningAction SilentlyContinue
        if ($test.TcpTestSucceeded) { $ready = $true; break }
    }
    if (-not $ready) {
        Write-Host "Local Bot API Server 10 秒內沒有起來，看一下 $logFile 的錯誤訊息" -ForegroundColor Red
        Write-Host "常見原因：api_id/api_hash 錯誤，或忘記先對舊 bot 呼叫 logOut。" -ForegroundColor Red
        throw "server not ready"
    }
    Write-Host "Local Bot API Server 已就緒（port 8081）" -ForegroundColor Green

    Write-Host "啟動課堂錄音助理...（Ctrl+C 結束）" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "class_assistant.py")
}
finally {
    Write-Host "關閉 Local Bot API Server..." -ForegroundColor Cyan
    if ($serverProc -and -not $serverProc.HasExited) {
        Stop-Process -Id $serverProc.Id -Force
    }
}
