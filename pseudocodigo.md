# Pseudocódigo del simulador de parqueadero

Refleja el diseño actual: cola FIFO, esperas cancelables, salida anticipada,
despachador único de eventos, métricas y registro en CSV.

Convenciones: `LOCK` = `threading.Lock`, `SEM` = `BoundedSemaphore`,
`STOP` = Event global de parada, `EXIT_i` = Event de salida del vehículo *i*.
Un hilo **nunca** mantiene un LOCK mientras duerme o espera.

---

## 1. Datos compartidos (ParkingLot)

```text
espacios        ← lista de N posiciones, todas vacías
cola_espera     ← cola FIFO vacía
SEM             ← BoundedSemaphore(N)        # permisos = plazas libres
LOCK            ← Lock()                     # protege espacios, cola y estados
```

## 2. Ciclo de vida de un vehículo (un hilo por vehículo)

```text
VehicleWorker.run():
    estado ← LLEGANDO;  publicar(vehicle_approaching)

    # Espera aleatoria antes de pedir acceso. Cancelable: no es sleep().
    si STOP.wait(timeout = retraso_llegada):
        estado ← CANCELADO;  publicar(vehicle_cancelled);  terminar

    inicio_espera ← reloj_monotónico()
    publicar(vehicle_waiting)
    plaza ← ParkingLot.occupy_space(vehículo, STOP)      # ver sección 3

    tiempo_espera ← reloj_monotónico() − inicio_espera   # no incluye retraso_llegada
    si plaza es NINGUNA:
        publicar(vehicle_cancelled, reason = entry_cancelled);  terminar

    publicar(vehicle_parked, plaza, tiempo_espera)

    motivo ← parking_duration_elapsed
    si STOP.is_set():                                    # la parada llegó justo al entrar
        motivo ← simulation_stopped
    sino si EXIT_i.wait(timeout = duración_permanencia): # salida manual o parada
        motivo ← motivo_pedido_en(request_exit)

    # Bloque finally: se ejecuta SIEMPRE que hubo plaza, incluso con errores.
    ParkingLot.mark_exiting(id);  publicar(vehicle_exiting, motivo)
    ParkingLot.release_space(id); publicar(vehicle_finished, motivo)
```

`request_exit(motivo)`: acepta la petición solo si el vehículo está ESTACIONADO
y no tiene otra pendiente; guarda el motivo y activa `EXIT_i`.

## 3. Entrada con cola FIFO (ParkingLot.occupy_space)

```text
occupy_space(vehículo, STOP, poll):
    LOCK:
        si el vehículo ya está registrado o su estado no permite entrar: error
        si STOP.is_set(): estado ← CANCELADO;  devolver NINGUNA
        encolar(vehículo);  estado ← EN_ESPERA

    adquirido ← falso;  asignado ← falso
    intentar:
        repetir:
            si STOP.is_set(): devolver NINGUNA
            LOCK:
                es_primero ← (cabeza de cola_espera es este vehículo)
                si es_primero y SEM.acquire(sin bloquear):
                    adquirido ← verdadero
                    si STOP.is_set():                    # cancelación tardía
                        SEM.release();  adquirido ← falso;  devolver NINGUNA
                    i ← primer índice libre de espacios
                    espacios[i] ← vehículo;  sacar_de_cola(vehículo)
                    plaza ← i + 1;  estado ← ESTACIONADO
                    adquirido ← falso;  asignado ← verdadero
                    devolver plaza
            # Pausa FUERA del LOCK y cancelable:
            si STOP.wait(timeout = poll): devolver NINGUNA
    finalmente:
        LOCK:
            si adquirido: SEM.release()                  # nunca se pierde un permiso
            si no asignado: quitar_de_cola(vehículo);  estado ← CANCELADO
```

Solo la cabeza de la cola intenta `acquire`: así el orden de entrada es FIFO
(equivale a FCFS), cosa que el semáforo por sí solo no garantiza.
Costo: puede pasar hasta `poll` entre una salida y la siguiente entrada.

```text
release_space(id):
    LOCK:
        i ← plaza de id (si no está estacionado: error)
        espacios[i] ← vacío;  estado ← FINALIZADO
        SEM.release()                       # BoundedSemaphore detecta releases de más
        devolver i + 1
```

## 4. Controlador (SimulationController)

```text
start_simulation():
    LOCK:
        si ya está corriendo: devolver falso
        si quedan hilos de la ejecución anterior: error (la UI usa is_idle)
        STOP.clear();  corriendo ← verdadero;  publicar(simulation_started)
        si AUTO_GENERATION: lanzar hilo generador (daemon)

generador():                                # hilo propio
    mientras no STOP.is_set():
        add_vehicle()
        intervalo ← aleatorio(min, max)     # con el RNG propio y semilla
        si STOP.wait(intervalo): salir      # espera interrumpible
    publicar(generation_stopped)

add_vehicle(retraso = ninguno):
    LOCK:
        si no corriendo o STOP.is_set(): error
        id ← "V" + contador;  tiempos ← aleatorios con el RNG
        crear Vehicle, VehicleWorker y Thread(daemon);  registrar;  publicar(vehicle_created)
        iniciar hilo (si falla: deshacer el registro)

stop_simulation():
    LOCK: corriendo ← falso;  STOP.set();  publicar(simulation_stopping)
    # ORDEN OBLIGATORIO: primero STOP, después las salidas.
    para cada worker: worker.request_exit("simulation_stopped")

reset_simulation(timeout):
    stop_simulation()
    esperar (join) a TODOS los hilos, máximo timeout
    verificar: 0 plazas ocupadas y 0 vehículos en espera
    LOCK: vaciar registros;  contador ← 0;  RNG ← nuevo con la misma semilla
    publicar(simulation_reset)

shutdown(timeout):                          # cerrar la ventana
    stop_simulation();  esperar hilos;  publicar(controller_shutdown, clean)
```

## 5. Eventos, métricas, registro y despachador

```text
# Muchos productores (workers, controller) → una cola → UN consumidor.
event_queue ← Queue()

EventDispatcher.drain(máximo = 200):        # lo llama la UI con after()
    mientras procesados < máximo y la cola no esté vacía:
        evento ← event_queue.get_nowait()
        para cada manejador en [logger.log, metrics.consume, ventana.on_event]:
            intentar manejador(evento)
            si falla: guardar el error y SEGUIR con el siguiente manejador

Metrics.consume(evento):
    vehicle_waiting   → agregar a "en cola"; actualizar cola máxima
    vehicle_parked    → pasar a "estacionados"; acumular tiempo de espera
    vehicle_finished  → quitar de estacionados; contar atendido y motivo de salida
    vehicle_cancelled → quitar de la cola; contar cancelado
    antes de cada evento: area ← area + estacionados × (t − t_anterior)
    utilización ← area / (capacidad × duración)
    simulation_reset  → todo en cero

EventLogger.log(evento):
    fila ← columnas fijas + "extra" (JSON con el resto); neutralizar "=+-@"
    si el timestamp no es válido: usar la hora actual
    escribir + flush;  si falla el archivo: desactivarse sin lanzar excepción
    simulation_reset → número de sesión + 1
```

## 6. Programa principal (main.py)

```text
backend ← controller + metrics + logger (CSV en logs/events.csv)
ventana ← MainWindow(root, controller, metrics)
despachador ← EventDispatcher(cola, [logger.log, metrics.consume, ventana.on_event])

tick():                                     # cada UI_REFRESH_INTERVAL_MS, hilo principal
    despachador.drain();  ventana.refresh()
    root.after(UI_REFRESH_INTERVAL_MS, tick)

al cerrar la ventana:
    cancelar tick;  controller.shutdown();  despachador.drain_all();  logger.close();  destruir ventana
```
