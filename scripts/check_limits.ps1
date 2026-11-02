param([ValidateSet('mongo','postgres')][string]$Engine = 'mongo', [string]$Project = 'eva02-jsonbench-smoke', [string]$Output = 'evidence/smoke')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
$id = docker compose --env-file .env --env-file config/images.env -p $Project ps -q $Engine
if (!$id) { throw 'Motor no activo' }
$label = docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' $id
if ($label -ne $Project) { throw 'Proyecto distinto: no operar' }
$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
New-Item -ItemType Directory -Force -Path $Output | Out-Null
docker inspect --format 'image={{.Image}} cpus={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}} memoryswap={{.HostConfig.MemorySwap}} oom={{.State.OOMKilled}} pid={{.State.Pid}}' $id | Set-Content "$Output/$stamp-$Engine-inspect.txt"
docker exec $id sh -c 'cat /proc/self/cgroup; cat /sys/fs/cgroup/cpu.max; cat /sys/fs/cgroup/cpu.stat; cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/memory.swap.max; cat /sys/fs/cgroup/memory.events; cat /sys/fs/cgroup/memory.stat; df -h /' | Set-Content "$Output/$stamp-$Engine-cgroup.txt"
if ($LASTEXITCODE -ne 0) { throw 'Lectura cgroup falló' }
Get-Content "$Output/$stamp-$Engine-inspect.txt"
Get-Content "$Output/$stamp-$Engine-cgroup.txt" -TotalCount 22
