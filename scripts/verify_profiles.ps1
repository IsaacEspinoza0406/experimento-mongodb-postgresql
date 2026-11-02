param([ValidateSet('mongo','postgres')][string]$Engine = 'postgres', [string]$Project = 'eva02-jsonbench-smoke')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$id = docker compose --env-file .env --env-file config/images.env -p $Project ps -q $Engine
if (!$id) { throw 'Motor no activo' }
$label = docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' $id
if ($label -ne $Project) { throw 'Etiqueta distinta' }
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
foreach ($profile in @(@('A',4,'4096m',4294967296),@('B',2,'2048m',2147483648),@('C',1,'1024m',1073741824))) {
    docker update --cpus $profile[1] --memory $profile[2] --memory-swap $profile[2] $id | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'docker update falló' }
    $limits = docker exec $id sh -c 'cat /sys/fs/cgroup/cpu.max; cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/memory.swap.max'
    $limits | Set-Content "evidence/smoke/$stamp-$Engine-profile-$($profile[0]).txt"
    $expectedCpu = "$([int]$profile[1] * 100000) 100000"
    if ($limits[0] -ne $expectedCpu -or $limits[1] -ne [string]$profile[3] -or $limits[2] -ne '0') { throw "Límites efectivos distintos: $limits" }
    Write-Output "$Engine perfil $($profile[0]): $limits"
}
# Compatibilidad swappiness se prueba separadamente con docker run; update no ofrece esa opción.
