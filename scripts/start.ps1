param([string[]]$TaskArguments = @('dry-run','--seconds','5'))
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root
$stamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
$log = Join-Path $root "evidence/persistence/$stamp"
New-Item -ItemType Directory -Path $log | Out-Null
# Argumentos acotados: no se construye código de shell desde texto del usuario.
foreach ($value in $TaskArguments) {
    if ($value -notmatch '^[A-Za-z0-9_./:-]+$') { throw 'Argumento no permitido' }
}
$python = Join-Path $root '.venv/bin/python.exe'
if (!(Test-Path $python)) { throw 'Falta Python local fijado .venv/bin/python.exe' }
$process = Start-Process -FilePath $python -ArgumentList (@('-u','scripts/orchestrate.py') + $TaskArguments) -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput "$log/stdout.txt" -RedirectStandardError "$log/stderr.txt"
[ordered]@{pid=$process.Id; started=(Get-Date -Format o); arguments=$TaskArguments; logs=$log} | ConvertTo-Json | Set-Content "$log/process.json"
Write-Output "PID=$($process.Id); logs=$log; seguimiento: python scripts/orchestrate.py status"
