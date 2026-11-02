param([string]$Project = 'eva02-jsonbench', [int]$TestSeconds = 0)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root
if ($Project -notin @('eva02-jsonbench','eva02-deadline-test')) { throw 'Proyecto no permitido' }
if ($TestSeconds -gt 0 -and $Project -ne 'eva02-deadline-test') { throw 'Override solo de prueba' }
$stamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
$base = 'evidence/deadline/20261101'
$directory = if ($TestSeconds -gt 0) { "evidence/deadline-test/$stamp" } else { "$base/attempt-$stamp" }
$pointer = Join-Path $base 'current.json'
if ($TestSeconds -eq 0 -and (Test-Path $pointer)) {
    $state = Get-Content $pointer -Raw | ConvertFrom-Json
    $runtimeFile = Join-Path $state.directory 'process.json'
    $runtime = if (Test-Path $runtimeFile) { Get-Content $runtimeFile -Raw | ConvertFrom-Json } else { $state }
    if (Get-Process -Id $runtime.pid -ErrorAction SilentlyContinue) {
        Write-Output "Vigilante existente PID=$($runtime.pid), $($state.directory)"
        exit 0
    }
    Write-Output 'Vigilante anterior terminó; logs preservados, nuevo intento separado.'
}
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$arguments = @('-u','scripts/deadline_guard.py','--project',$Project,'--output',$directory)
if ($TestSeconds -gt 0) { $arguments += @('--test-seconds', [string]$TestSeconds) }
$python = Join-Path $root '.venv/bin/python.exe'
if (!(Test-Path $python)) { throw 'Falta Python local fijado' }
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput "$directory/stdout.txt" -RedirectStandardError "$directory/stderr.txt"
if ($TestSeconds -eq 0) { [ordered]@{pid=$process.Id; directory=$directory} | ConvertTo-Json | Set-Content $pointer }
Write-Output "Vigilante PID=$($process.Id), $directory"
