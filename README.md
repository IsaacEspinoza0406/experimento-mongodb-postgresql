# Experimento MongoDB frente a PostgreSQL JSONB

Repositorio de código del entregable de **Héctor Isaac Espinoza Mendoza, matrícula 243751**, Desarrollo de Software, Bases de Datos en la Nube, Universidad Politécnica de Chiapas. Fecha: 1 de noviembre de 2026.

**Ejecución parcial real: 32 de 54 escenarios, una repetición por escenario completado.** Quedaron 22 escenarios pendientes de la primera vuelta. Se conservaron localmente 39 intentos definitivos: 32 completos auditados y 7 inválidos, que afectan seis escenarios únicos; cinco se recuperaron. Los intentos inválidos no cuentan como resultados válidos. El reporte, CSV y evidencias se entregan por separado y no están en este repositorio.

La matriz nominal del manual tiene 108 corridas (36 combinaciones × 3 repeticiones). Incluir ambas variantes de Q2 produce 54 escenarios × 3 = 162 corridas. Los 32 escenarios medidos una vez no cumplen ninguna de las dos matrices completas. No se promedian percentiles para inventar un percentil global; una sola repetición limita la comparación y no permite estimar variabilidad entre repeticiones.

## Contenido y protocolo

- [docker-compose.yml](docker-compose.yml): un motor a la vez, runner y telemetría fuera del cgroup del motor, volúmenes aislados por proyecto.
- [synthetic_data_generator.py](synthetic_data_generator.py): 500 000 órdenes sintéticas, semilla 243751 y manifiesto SHA256. No usa datasets de otros proyectos.
- [benchmark_runner/](benchmark_runner/): carga, comprobación de documentos idénticos, equivalencia, ejecución multiproceso, métricas y planes.
- [queries/](queries/): consultas originales Q1/Q2 y esquema `orders(id UUID PRIMARY KEY, data JSONB)`.
- [config/](config/): imágenes fijadas por versión y digest, Prometheus, perfiles y orden equilibrado de ejecución.
- [scripts/](scripts/): auditoría, control exclusivo, pausa, recuperación acotada, vigilante y respaldos locales.
- [tests/](tests/): pruebas unitarias del generador, métricas, equivalencia y controles, separadas de mediciones académicas.

Definitivos y piloto: **500 000 documentos idénticos; 60 s de calentamiento, 300 s de medición y 60 s de descanso**. Perfiles A/B/C: 4/2/1 CPU y 4096/2048/1024 MiB; concurrencias 1, 10 y 50. Q1 tiene variante `default`; Q2 MongoDB `nodisk`/`disk` (`allowDiskUse=false/true`) y PostgreSQL `4MB`/`64MB` (`work_mem`). No hay índice sobre el total de Q2. Las cifras desconocidas quedan vacías, nunca se imputan como cero. Los errores experimentales, timeout, OOM y spill requieren evidencia específica; un error no implica OOM y un I/O no demuestra spill.

El campo usado es `payment.final_total`; la mención a `payment.totals` se interpretó como discrepancia documental. El índice temporal PostgreSQL es textual: el cast directo a `timestamptz` no cumple la inmutabilidad requerida para el índice. Q2 mantiene el cast original; no se presume que use ese índice. En cgroups v2 se verificó `memory.swap.max=0` mediante `memswap_limit=mem_limit`; no se afirma soporte de `mem_swappiness`. El flush del kernel no vacía cachés internas de los motores ni la caché Windows. Node Exporter observa la VM Linux, no el host Windows. El informe separado documenta las limitaciones temporales y de telemetría de las corridas históricas.

## Preparar una reproducción nueva

Entorno implementado y probado: Windows + Docker Desktop con backend WSL2, distribución `docker-desktop`, kernel/cgroups v2. El flush automático está implementado específicamente para este entorno; en Linux nativo necesita una adaptación explícita y revalidación. Python coordinador 3.12 y Docker Compose v2 son necesarios. Imágenes y digests históricos en [images.lock.json](config/images.lock.json); no resolver tags flotantes para comparar resultados.

Desde la raíz, en PowerShell:

```powershell
python -m venv .venv
# Python Windows habitual; en MSYS el ejecutable es .venv/bin/python.exe.
.venv/Scripts/python.exe -m pip install -r requirements-coordinator.txt
.venv/Scripts/python.exe scripts/local_env.py
docker compose --env-file .env --env-file config/images.env config --quiet
docker compose --env-file .env --env-file config/images.env build runner
```

[local_env.py](scripts/local_env.py) crea `.env` exclusivo sin mostrar la contraseña ni sobrescribir uno existente. Alternativa: copiar [.env.example](.env.example) y sustituir el marcador. No imprimir la configuración expandida con secretos. Los drivers están fijados en [requirements.txt](requirements.txt), instalados en la imagen [Dockerfile](Dockerfile). Los motores no publican puertos; Prometheus se limita a `127.0.0.1:19090`.

```powershell
python synthetic_data_generator.py --count 500000 --seed 243751 --output data/orders-500000.jsonl
docker compose --env-file .env --env-file config/images.env -p eva02-jsonbench up -d --wait mongo
docker compose --env-file .env --env-file config/images.env -p eva02-jsonbench run --rm runner -m benchmark_runner.load --engine mongodb --dataset data/orders-500000.jsonl --evidence evidence/full/mongo-load.json
docker compose --env-file .env --env-file config/images.env -p eva02-jsonbench run --rm runner -m benchmark_runner.validate --engine mongodb --dataset data/orders-500000.jsonl --load-evidence evidence/full/mongo-load.json --evidence evidence/full/mongo-equivalence.json
docker compose --env-file .env --env-file config/images.env -p eva02-jsonbench stop mongo
```

Repetir la carga y validación para PostgreSQL: servicio `postgres`, `--engine postgresql`, evidencias `evidence/full/postgres-load.json` y `evidence/full/postgres-equivalence.json`, con MongoDB detenido. El loader exige destino vacío y verifica conteos y hashes del multiconjunto completo. No repetir automáticamente una carga parcial. Conservar sus logs. Cada consulta consume todos sus resultados. El generador rechaza sobrescribir un archivo, intento parcial o manifiesto existente.

Levantar colectores con `docker compose --env-file .env --env-file config/images.env -p eva02-jsonbench up -d prometheus cadvisor node-exporter`. cAdvisor requiere privilegios y montajes del kernel; revisar esos accesos antes de usarlo en otro equipo. Verificar el ID exacto del motor con `python scripts/check_telemetry.py --container-id ID --output evidence/telemetry-new.json`. [check_limits.ps1](scripts/check_limits.ps1) comprueba límites efectivos; [verify_profiles.ps1](scripts/verify_profiles.ps1) aplica perfiles solo en pruebas separadas y exige motor preparado y directorio `evidence/smoke` creado.

Primero hacer smoke pequeño en proyecto `eva02-jsonbench-smoke`, dataset y evidencias separados. `--mode smoke` usa 2/5/2 s y no produce resultados académicos. Consultar [matrix.json](config/matrix.json) para claves exactas. Ejemplo de ejecución con motor ya preparado:

```powershell
python scripts/orchestrate.py run --mode smoke --project eva02-jsonbench-smoke --key postgresql-PROFILE_A_BASELINE-Q1-default-c1-r1
```

Para piloto usar `--mode pilot --project eva02-jsonbench --allow-kernel-flush` con una clave de la matriz, ambos motores ya cargados/validados y el motor seleccionado activo. El flush es una operación sobre el kernel compartido de Docker Desktop: requiere revisión previa, verifica `boot_id` común y rechaza contenedores ajenos o clientes activos. No ejecutar ese parámetro sin comprender su alcance. Las adaptaciones de entorno requieren nueva validación.

```powershell
python scripts/audit_run.py results/pilot/ID_DEL_INTENTO
python scripts/accept_pilot.py results/pilot/ID_DEL_INTENTO
```

La aceptación se genera desde un piloto real auditado y vincula hash de código, dataset e imágenes. **No se distribuye una aceptación histórica preaprobada.** `config/acceptance.json`, los manifiestos de carga y las equivalencias son archivos generados obligatorios, no dependencias olvidadas. El piloto también exige el vigilante independiente activo; iniciarlo con `./scripts/start_guard.ps1` solo tras revisar la ventana y las rutas indicadas a continuación.

## Horario, persistencia y recuperación

Se conserva [deadline.json](config/deadline.json): **1 de noviembre de 2026, 22:00 America/Mexico_City; cancelación preventiva 21:59**. Esa ventana ya venció: el código no autoriza nuevas mediciones. Para otra campaña se debe acordar y revisar explícitamente la fecha, las rutas fechadas `20261101` del vigilante y su comprobación en el orquestador; verificar controles y generar nueva aceptación antes de medir. No basta con cambiar el reloj del equipo.

[start.ps1](scripts/start.ps1) y [start_guard.ps1](scripts/start_guard.ps1) esperan `.venv/bin/python.exe`, la ruta MSYS del equipo original. En Python Windows habitual ajustar ambos a `.venv/Scripts/python.exe` antes del piloto. Ejecutan procesos ocultos con logs persistentes; no son servicios que sobrevivan un reinicio. No iniciar dos controladores. El lock por creación exclusiva impide duplicados; no quitarlo basándose solo en su antigüedad.

Después de verificar ventana, salud, permisos y aceptación, los comandos de operación son:

```powershell
./scripts/start_guard.ps1
./scripts/start.ps1 -TaskArguments @('matrix','--mode','final','--project','eva02-jsonbench','--allow-kernel-flush')
python scripts/orchestrate.py status --mode final
python scripts/orchestrate.py stop --mode final
```

[run_benchmarks.sh](run_benchmarks.sh) admite los mismos argumentos del orquestador en shell. El orden prioriza los 54 escenarios en bloques equilibrados entre motores y perfiles; solo después admite hasta 18 segundas repeticiones c50. Se omiten claves completas auditadas, no se borran intentos previos. El controlador guarda manifiesto, solicitudes crudas, CSV exacto, telemetría y logs por intento. El vigilante retira únicamente carga etiquetada del proyecto; guarda evidencia de cancelación incompleta.

La parada solicitada deja terminar medición, descanso y guardado. Para reanudar, comprobar PID, Docker, heartbeat y logs recientes; retirar `results/final/STOP_BETWEEN_RUNS` solo tras verificar que no hay otro controlador y que se desea continuar. Un intento inconcluso debe diagnosticarse y auditarse, no declararse válido. Los volúmenes se conservan; no usar `down -v` ni `docker system prune`.

Recuperación acotada: conserva intentos inválidos; solo un reintento por clave si el fallo es recuperable y los colectores se recuperan. Tras dos fallos, registra incidente pendiente y continúa solo con telemetría sana. Si persiste la avería, detiene carga y deja `BLOCKED.json`. Timeouts PostgreSQL 57014 solo admiten esta vía cuando todos los errores crudos coinciden con `statement timeout`; no se reclasifican errores arbitrarios como telemetría.

Una matriz activa necesita heartbeat reciente, fase activa, proceso real y evidencia de avance de CPU/solicitudes/logs. `blocked`, `paused_between_runs`, `deadline_stop`, `first_pass_incomplete` y `finished` son estados terminales. La admisión exige margen para el ciclo completo y evidencia antes del corte.

## Verificar código sin nuevas mediciones

```powershell
python -m unittest discover -s tests -v
python scripts/orchestrate.py --help
```

Las pruebas unitarias no levantan Docker ni ejecutan la matriz. [audit_run.py](scripts/audit_run.py) recalcula cada CSV desde solicitudes. [consolidate.py](scripts/consolidate.py) separa variantes sin promediar percentiles; para análisis se requieren auditorías aprobadas, no basta `status=complete`.

## Alcance de esta distribución

Es una exportación con historial limpio: no incluye commits del repositorio de trabajo que contenían resultados, documentos o evidencias. Todos esos archivos se conservaron localmente. Se excluyen dataset, resultados, output, reportes, archivos comprimidos, evidencias, secretos, volúmenes y entornos virtuales mediante [.gitignore](.gitignore).

Dos ajustes posteriores a la medición permiten distribuir solo código: el orquestador crea `docs/memoria` cuando lo necesita y [report.py](scripts/report.py) invoca la consolidación ligera en lugar de empaquetar documentos académicos ausentes. Se añadió [tzdata 2025.2](https://pypi.org/project/tzdata/2025.2/) como respaldo de zonas horarias para Windows (disponibilidad consultada el 1 de noviembre de 2026). No se modificaron runner, consultas, generador, métricas, criterios de validez ni tiempos medidos. El hash de esta distribución difiere del congelado histórico: una reproducción necesita su propia aceptación; no se afirma que esta exportación sea el árbol exacto de las mediciones entregadas.
