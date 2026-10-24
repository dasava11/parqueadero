# Simulador de Parqueadero Inteligente

Simulador de un parqueadero con **hilos en Python** e interfaz gráfica en **Tkinter**.
Cada vehículo es un hilo que compite por un recurso limitado (las plazas), igual que
los procesos de un Sistema Operativo compiten por la CPU o la memoria. El proyecto
ilustra **concurrencia, sincronización, exclusión mutua, bloqueo y planificación**.

> Proyecto final de Arquitectura de Computadores.
> Autor: _<tu nombre>_ · Video explicativo: _<enlace pendiente>_ · Informe PDF: _<pendiente>_

---

## 1. Estado del proyecto

| Componente | Estado | Notas |
|---|---|---|
| `config.py` | Hecho | Parámetros y validaciones (incluye `RANDOM_SEED`). Sondeo de la cola en 0.1 s. |
| `models/vehicle.py` | Hecho | `Vehicle` y `VehicleStatus` (incluye `CANCELLED`). |
| `core/parking.py` | Hecho y probado | Semáforo + Lock + cola FIFO. Devuelve copias de los vehículos. |
| `threads/vehicle_worker.py` | Hecho y probado | Ciclo de vida del vehículo; `request_exit(reason)`. |
| `core/controller.py` | Hecho y probado | Semilla propia, copias, daemon, `is_idle`, salida manual validada. |
| `services/dispatcher.py` | Hecho y probado | Único consumidor de la cola de eventos. |
| `services/event_logger.py` | Hecho y probado | CSV en `logs/events.csv`; tolera timestamps inválidos. |
| `services/metrics.py` | Hecho y probado | Espera, ocupación, cola, utilización, atendidos y cancelados. |
| `main.py` | Hecho (probado sin Tkinter) | Une backend, ventana y despachador; cierre ordenado. Falta probarlo con la interfaz real. |
| `ui/*` | Por revisar | Ventana principal, barra de controles, métricas y vista del parqueadero. |
| Pruebas | Hechas | `tests/`: métricas, despachador, logger, integración sin GUI y `main.py`. |
| `pseudocodigo.md` | Actualizado | Refleja FIFO, cancelación, salida anticipada y despachador. |

---

## 2. Analogía con Sistemas Operativos

| Parqueadero | Sistema Operativo |
|---|---|
| Vehículo (un hilo) | Proceso que solicita un recurso |
| Plazas de parqueo | Recurso finito (CPU, memoria) |
| `BoundedSemaphore(capacidad)` | Control de acceso al recurso |
| `Lock` | Exclusión mutua en la sección crítica |
| Vehículo en la cola de espera | Proceso bloqueado esperando el recurso |
| Cola FIFO | Política de planificación FCFS |
| Vehículo que sale y libera la plaza | Proceso que termina y libera el recurso |
| Cola de eventos + despachador | Cola de mensajes entre procesos con un único receptor |

**Precisión importante:** el programa es **un solo proceso de Python con varios hilos**.
La equivalencia vehículo = proceso es una abstracción didáctica. Además, por el GIL de
CPython hay **concurrencia** (tareas con tiempos solapados), no paralelismo real de CPU.

---

## 3. Requisitos y ejecución

- **Python 3.10 o superior** (se usan anotaciones como `int | None`).
- **Tkinter** (viene con Python). Para comprobarlo: `python3 -m tkinter` debe abrir una ventana de prueba.
  En macOS con Python de Homebrew, si falta, instala `python-tk` (por ejemplo `brew install python-tk`).
- No hay dependencias externas: solo biblioteca estándar.

```bash
cd parqueadero
python3 main.py                             # simulador con interfaz
python3 tests/test_metrics_dispatcher.py    # métricas, despachador y logger
python3 tests/test_integration.py           # integración sin GUI (~26 s)
python3 tests/test_main.py                  # main.py sin Tkinter
```

Todos los imports son relativos a la raíz del proyecto (`from core.parking import ...`),
por eso el programa y las pruebas deben ejecutarse desde esa carpeta.

---

## 4. Estructura del proyecto

```text
parqueadero/
├── main.py                  # punto de entrada: conecta backend, ventana y despachador
├── config.py                # parámetros globales y validación
├── pseudocodigo.md          # pseudocódigo del simulador
├── README.md
├── core/
│   ├── parking.py           # ParkingLot: plazas, cola FIFO, Semaphore, Lock
│   └── controller.py        # SimulationController: crea vehículos e hilos
├── models/
│   └── vehicle.py           # Vehicle (dataclass) y VehicleStatus (Enum)
├── threads/
│   └── vehicle_worker.py    # VehicleWorker: ciclo de vida dentro de su hilo
├── services/
│   ├── dispatcher.py        # EventDispatcher: único consumidor de la cola de eventos
│   ├── event_logger.py      # registro de eventos (CSV)
│   └── metrics.py           # cálculo de métricas
├── ui/
│   ├── main_window.py       # ventana principal
│   ├── nav_bar.py           # botones de control
│   ├── metrics_board.py     # contadores y estadísticas
│   └── parking_view.py      # plazas, cola y semáforo visual
├── tests/
│   ├── test_metrics_dispatcher.py
│   ├── test_integration.py
│   └── test_main.py
└── logs/                    # CSV generados (ignorados por git)
```

### Responsabilidades (principio de responsabilidad única)

| Responsabilidad | Dónde vive |
|---|---|
| Crear vehículos (IDs, tiempos aleatorios, hilos) | `core/controller.py` |
| Ejecutar el ciclo de vida de un vehículo | `threads/vehicle_worker.py` |
| Gestionar plazas, permisos, ocupación y cola | `core/parking.py` |
| Datos y estado de un vehículo | `models/vehicle.py` |
| Retirar cada evento de la cola y reenviarlo | `services/dispatcher.py` |
| Calcular métricas | `services/metrics.py` |
| Registrar eventos | `services/event_logger.py` |
| Conectar las piezas, bucle `after()` y cierre | `main.py` |
| Dibujar la interfaz | `ui/*` |

`Vehicle` son los **datos**, `VehicleWorker` es el **trabajo** que realiza el vehículo y
`Thread` es el **hilo de ejecución** donde se realiza ese trabajo.

---

## 5. Concurrencia y sincronización

| Mecanismo | Dónde | Función |
|---|---|---|
| `BoundedSemaphore(capacidad)` | `ParkingLot` | Cuenta los permisos (plazas libres). Detecta cualquier `release` de más. |
| `Lock` | `ParkingLot` | Protege plazas, cola de espera y estados. La sección crítica es corta: **nunca** se mantiene durante esperas ni `sleep`. |
| `Lock` | `SimulationController` | Protege los registros de vehículos, workers e hilos, el generador aleatorio y el contador de IDs. |
| `Lock` | `EventLogger`, `Metrics` | Protegen su estado interno. |
| `Event` de parada global | Controller → todos los workers | Cancela la espera de llegada y la espera de plaza. |
| `Event` individual de salida | Cada `VehicleWorker` | Termina antes la permanencia (salida manual o parada de la simulación). |
| `queue.Queue` de eventos | Workers y controller → despachador | Comunicación segura con Tkinter. |

Reglas que se respetan en todo el código:

1. Los hilos secundarios **nunca** tocan widgets de Tkinter; solo publican eventos.
2. El Lock **no** se mantiene mientras se espera el semáforo o se duerme.
3. El permiso del semáforo se devuelve solo si ese hilo lo adquirió (`try/finally`).
4. Las esperas usan `Event.wait(timeout)`; no hay bucles `while True` sin pausa.
5. Nunca hay `sleep` largo en el hilo principal.
6. La cola de eventos tiene **un único consumidor**: el despachador.

### Política de acceso: cola FIFO

El semáforo de Python no garantiza en qué orden se despiertan los hilos. Para que la cola
visual coincida con quién entra realmente, `ParkingLot` mantiene una cola FIFO y **solo el
primer vehículo de la cola** intenta `acquire(blocking=False)`; entre intentos espera
`PARKING_ACQUIRE_POLL_INTERVAL_SECONDS` (0.1 s) usando `stop_event.wait()`, de modo que la
espera es cancelable. Consecuencia: puede pasar hasta ese intervalo entre una salida y la
siguiente entrada (espera con sondeo, no bloqueo real).

### Ciclo de vida de un vehículo

```text
GENERATED -> APPROACHING -> WAITING -> PARKED -> EXITING -> FINISHED
                 |            |
                 +------------+--> CANCELLED   (la simulación se detuvo antes de estacionarse)
```

1. Espera `arrival_delay` (cancelable con el Event global).
2. Entra a la cola FIFO y espera una plaza.
3. Estacionado: permanece `parking_duration`, o hasta recibir `request_exit()`.
4. Marca `EXITING`, libera la plaza y devuelve el permiso.
5. Publica `vehicle_finished` y el hilo termina.

### Despachador único de eventos

Una `queue.Queue` entrega cada evento a **un solo** `get()`: si el logger, las métricas y
la vista la leyeran por separado, se repartirían los eventos. `EventDispatcher` retira
cada evento una vez y lo reenvía, en orden, a todos los manejadores
(`logger.log`, `metrics.consume`, `ventana.on_event`). Vive en su propio módulo para:
tener responsabilidad única, aislar fallos (si un manejador falla, los demás siguen),
poder probarse sin interfaz y acotar el trabajo por ciclo (`max_events_per_tick`) para no
congelar Tkinter.

---

## 6. Eventos

Todos los eventos son diccionarios con al menos `type` y `timestamp` (segundos epoch).
Los de vehículo incluyen también `vehicle_id`.

| Evento | Origen | Datos adicionales |
|---|---|---|
| `simulation_started` | controller | `auto_generation` |
| `vehicle_created` | controller | `arrival_delay`, `parking_duration` |
| `vehicle_approaching` | worker | `status`, `arrival_delay` |
| `vehicle_waiting` | worker | `status` |
| `vehicle_parked` | worker | `parking_space`, `waiting_time`, `parking_duration` |
| `vehicle_exit_requested` | controller | |
| `vehicle_exiting` | worker | `parking_space`, `parking_time`, `reason` |
| `vehicle_finished` | worker | `released_space`, `waiting_time`, `parking_time`, `reason` |
| `vehicle_cancelled` | worker | `reason` (`simulation_stopped_before_arrival` o `entry_cancelled`), `waiting_time` |
| `vehicle_error` | worker | `error` |
| `simulation_stopping` | controller | |
| `generation_stopped` | controller | |
| `simulation_reset` | controller | |
| `controller_error` | controller | `error` |
| `controller_shutdown` | controller | `clean` |

Valores de `reason` en `vehicle_exiting` y `vehicle_finished`:
`parking_duration_elapsed`, `manual_exit`, `simulation_stopped`.

`vehicle_exit_requested` puede llegar a la cola después de `vehicle_exiting`; los
consumidores no deben suponer ese orden.

---

## 7. Detener, reiniciar y cerrar

| Acción | Qué hace |
|---|---|
| **Iniciar** | Limpia el Event de parada, marca la simulación como activa y lanza el generador automático (si `AUTO_GENERATION_ENABLED`). Falla si aún quedan hilos de una ejecución anterior: la interfaz debe habilitarlo solo con `controller.is_idle`. |
| **Agregar vehículo** | `add_manual_vehicle()`: crea un `Vehicle`, su `VehicleWorker` y su `Thread`, con un retraso de llegada corto. Solo con la simulación activa. |
| **Detener** | Impide nuevas llegadas, cancela a quienes aún no se han estacionado y pide la salida de los estacionados (`simulation_stopped`). Los hilos terminan en milisegundos. |
| **Reiniciar** | Detiene, espera (`join`) a **todos** los hilos, verifica que no queden ocupadas ni esperando, y recién entonces limpia los registros y recrea el generador aleatorio con la misma semilla. |
| **Cerrar la ventana** | `Application.close()`: `shutdown()`, vaciado final de la cola, cierre del CSV y destrucción de la ventana. |

Como el reinicio espera a que **todos** los hilos antiguos terminen antes de reutilizar el
`ParkingLot`, ningún hilo viejo puede liberar permisos en la simulación nueva.

---

## 8. Configuración (`config.py`)

| Parámetro | Valor | Descripción |
|---|---|---|
| `PARKING_CAPACITY` | 20 | Plazas del parqueadero |
| `AUTO_GENERATION_ENABLED` | True | Genera vehículos automáticamente |
| `AUTO_ARRIVAL_INTERVAL_MIN/MAX_SECONDS` | 0.5 / 1.5 | Intervalo entre llegadas automáticas |
| `MANUAL_VEHICLE_ARRIVAL_DELAY_MIN/MAX_SECONDS` | 0 / 1 | Retraso del botón "Agregar vehículo" |
| `VEHICLE_ARRIVAL_DELAY_MIN/MAX_SECONDS` | 5 / 10 | Retraso individual antes de pedir acceso |
| `PARKING_DURATION_MIN/MAX_SECONDS` | 5 / 30 | Permanencia estacionado |
| `PARKING_ACQUIRE_POLL_INTERVAL_SECONDS` | 0.1 | Pausa entre intentos de la cola FIFO |
| `UI_REFRESH_INTERVAL_MS` | 100 | Periodo del `after()` de la interfaz |
| `RANDOM_SEED` | 42 | Semilla para tiempos reproducibles |
| `EVENT_LOG_PATH` | `logs/events.csv` | Archivo de registro |

**Carga esperada** (ley de Little, aproximada): llegada media ≈ 1 vehículo/s y permanencia
media 17.5 s dan ≈ 17.5 plazas ocupadas de 20 (~87 %). El sistema queda cerca de la
saturación y debería llenarse de forma intermitente. Es una estimación: confírmala con
`Metrics`. Si en la demo no se forma cola, prueba capacidad 10–12.

**Reproducibilidad:** con semilla, los tiempos de cada vehículo se repiten; el intercalado
exacto de los hilos no es determinista, así que los promedios serán parecidos, no idénticos.

---

## 9. Pendientes conocidos

1. **Poda de registros** (opcional): `_vehicles`, `_workers` y `_threads` del controller crecen durante la ejecución.
2. **Interfaz:** debe capturar `TimeoutError`/`RuntimeError` de reiniciar y cerrar y habilitar "Iniciar" solo con `controller.is_idle`.
3. **Orden de eventos:** `vehicle_exit_requested` puede llegar después de `vehicle_exiting`.

---

## 10. Métricas y registro de eventos

**Métricas (`Metrics.snapshot()`):** vehículos creados, atendidos y cancelados; motivos de
salida; ocupados y en cola ahora; ocupación y cola máximas; espera promedio y máxima;
estacionamiento promedio; utilización (plaza-segundos ocupadas / capacidad × duración).

**CSV de eventos** (`logs/events.csv`), una fila por evento:

```text
session, timestamp, datetime, elapsed_s, event_type, vehicle_id, status, parking_space,
arrival_delay, parking_duration, waiting_time, parking_time, reason, extra
```

La columna `session` aumenta con cada reinicio y `elapsed_s` cuenta desde el primer evento
de la sesión, así se pueden comparar experimentos en un mismo archivo. El logger nunca
interrumpe la simulación: si el archivo falla, se desactiva y guarda `last_error`.

---

## 11. Interfaz

Cuatro zonas:

1. **Barra superior:** Iniciar simulación, Agregar vehículo, Detener, Reiniciar.
2. **Contadores:** ocupados, libres y en espera.
3. **Plazas:** una celda por plaza (libre o con el ID del vehículo) y la cola de espera aparte.
4. **Estado de acceso:** semáforo visual (verde = hay plazas, amarillo = hay cola o sistema detenido, rojo = lleno).

Contrato con `main.py`: `MainWindow(root, controller, metrics)`, con `on_event(event)` y
`refresh()` opcionales (ver la documentación al inicio de `main.py`). Todos los valores
salen de **una sola fuente de verdad** (`ParkingLot.get_snapshot()`, `Metrics.snapshot()` y
los eventos), para que contadores, cuadrícula y semáforo nunca se contradigan. Las
actualizaciones ocurren solo en el hilo principal con `after()`.

Mejoras para el nivel destacado: colores por estado, alerta y sonido (`root.bell()`) al
llenarse, salida manual de un vehículo estacionado, panel de métricas.

---

## 12. Pruebas

Se ejecutan sin interfaz desde la raíz del proyecto:

| Archivo | Qué comprueba |
|---|---|
| `tests/test_metrics_dispatcher.py` | Cálculo de métricas con un escenario manual, datos inválidos, despachador con un manejador roto, CSV y robustez del logger (timestamps inválidos, fórmulas, sesiones). |
| `tests/test_integration.py` | Controller + workers + despachador + métricas + logger con tiempos reducidos: invariantes cada 5 ms, salida manual, detener, reanudar, tres reinicios, cierre, secuencias de eventos válidas y semilla reproducible. |
| `tests/test_main.py` | `Application` de `main.py` con un `root` y una ventana falsos: bucle `after()`, cierre ordenado (dos veces), cola vacía y aislamiento de una vista que falla. |

---

## 13. Guía para el análisis con Sistemas Operativos

- **Bloqueo y planificación:** vehículo sin plaza = proceso bloqueado; la cola FIFO equivale a FCFS. Compara con prioridades y con la inanición.
- **Sincronización:** ejemplo de condición de carrera evitada (dos salidas simultáneas actualizando un contador sin Lock).
- **Deadlock:** se evita al no mantener el Lock mientras se espera el semáforo y al usar un único recurso con un único Lock.
- **Event:** parada ordenada y salida anticipada sin matar hilos: se les avisa.
- **Productor-consumidor:** hilos y controller producen eventos; el despachador es el único consumidor.
- **Rendimiento:** experimentos variando capacidad y tasa de llegada con semilla fija; comparar espera promedio y utilización.
- **Límites del modelo:** un solo proceso, GIL, espera con sondeo frente a bloqueo real del SO, no determinismo del planificador.

---

## 14. Hoja de ruta

- [x] Configuración y modelo de vehículo
- [x] `ParkingLot` con semáforo, Lock y cola FIFO
- [x] `VehicleWorker` con salida anticipada y cancelación
- [x] `SimulationController`
- [x] Despachador único de eventos
- [x] `event_logger.py` y `metrics.py`
- [x] Prueba de integración sin interfaz
- [x] `main.py`
- [x] Pseudocódigo y README actualizados
- [ ] Revisar y completar la interfaz Tkinter (`ui/*`) contra el contrato de `main.py`
- [ ] Innovación (salida manual en la interfaz, política de congestión)
- [ ] Prueba de 60 s con los parámetros reales
- [ ] Experimentos y análisis con SO
- [ ] Informe PDF (2 páginas) con captura
- [ ] Video de 3 a 5 minutos y enlace

## 15. Entregables

1. Código fuente en Python (este repositorio).
2. Informe PDF de 2 páginas: sistema y analogía con SO, hilos y sincronización, captura del simulador.
3. Video explicativo de 3 a 5 minutos: ejecución en vivo, hilos/semáforos/locks y comparación con un SO.
4. Enlace del video (YouTube o Drive) junto con el código.
