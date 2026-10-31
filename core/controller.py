from dataclasses import replace
from queue import Queue
from random import Random
from threading import Event, Lock, Thread
from time import monotonic, time

from config import (
    AUTO_ARRIVAL_INTERVAL_MAX_SECONDS,
    AUTO_ARRIVAL_INTERVAL_MIN_SECONDS,
    AUTO_GENERATION_ENABLED,
    MANUAL_VEHICLE_ARRIVAL_DELAY_MAX_SECONDS,
    MANUAL_VEHICLE_ARRIVAL_DELAY_MIN_SECONDS,
    PARKING_CAPACITY,
    PARKING_DURATION_MAX_SECONDS,
    PARKING_DURATION_MIN_SECONDS,
    RANDOM_SEED,
    VEHICLE_ARRIVAL_DELAY_MAX_SECONDS,
    VEHICLE_ARRIVAL_DELAY_MIN_SECONDS,
)

from core.parking import ParkingLot
from models.vehicle import Vehicle
from threads.vehicle_worker import VehicleWorker


class SimulationController:
    """
    Coordina la creación de vehículos, los hilos y
    el inicio, la detención y el reinicio del simulador.

    Política de parada:
    - Detener: impide nuevas llegadas, cancela a quienes aún esperan
      y pide la salida de los vehículos estacionados.
    - Reiniciar: lo anterior, más esperar (join) a que TODOS los
      hilos terminen antes de limpiar los registros.

    Orden obligatorio al detener: primero se activa el Event de
    parada y después se solicita la salida de los estacionados.
    """

    def __init__(
        self,
        parking_lot: ParkingLot | None = None,
        event_queue: Queue[dict[str, object]] | None = None,
        seed: int | None = RANDOM_SEED,
    ) -> None:

        # Todos los vehículos comparten el mismo parqueadero.
        self.parking_lot = (
            parking_lot
            if parking_lot is not None
            else ParkingLot(capacity=PARKING_CAPACITY)
        )

        # Canal por el que los trabajadores notifican eventos.
        # La interfaz consultará esta cola desde el hilo principal.
        self.event_queue = (
            event_queue
            if event_queue is not None
            else Queue()
        )

        # Generador aleatorio propio y reproducible. Solo se usa
        # con self._lock adquirido, así que su acceso está serializado.
        self._seed = seed
        self._rng = Random(seed)

        # Registro de vehículos, trabajadores e hilos.
        self._vehicles: dict[str, Vehicle] = {}
        self._workers: dict[str, VehicleWorker] = {}
        self._threads: dict[str, Thread] = {}

        self._vehicle_counter = 0

        # Control de las operaciones internas del controlador.
        self._lock = Lock()

        # Señal compartida para detener nuevas llegadas
        # y cancelar vehículos que todavía están esperando.
        self._simulation_stop_event = Event()

        self._generation_thread: Thread | None = None
        self._running = False

    def _publish_event(
        self,
        event_type: str,
        **details: object,
    ) -> None:
        """Publica un evento sin modificar la interfaz gráfica."""

        self.event_queue.put(
            {
                "type": event_type,
                "timestamp": time(),
                **details,
            }
        )

    def _active_thread_names_unlocked(self) -> list[str]:
        """
        Nombres de los hilos que siguen vivos.

        Debe ejecutarse con self._lock adquirido.
        """

        names = [
            thread.name
            for thread in self._threads.values()
            if thread.is_alive()
        ]

        if (
            self._generation_thread is not None
            and self._generation_thread.is_alive()
        ):
            names.append(self._generation_thread.name)

        return names

    @property
    def is_running(self) -> bool:
        """Indica si la simulación admite nuevas llegadas."""

        with self._lock:
            return self._running

    @property
    def is_idle(self) -> bool:
        """
        Indica si no queda ningún hilo de la simulación activo.

        La interfaz puede usarlo para habilitar "Iniciar" solo
        cuando la ejecución anterior terminó por completo.
        """

        with self._lock:
            return not self._active_thread_names_unlocked()

    @property
    def vehicles(self) -> tuple[Vehicle, ...]:
        """
        Devuelve COPIAS de los vehículos registrados.

        Los trabajadores siguen modificando los objetos originales;
        la interfaz solo debe leer estas copias.
        """

        with self._lock:
            return self.parking_lot.copy_vehicles(
                self._vehicles.values()
            )

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        """Busca un vehículo por su identificador (devuelve una copia)."""

        with self._lock:
            vehicle = self._vehicles.get(vehicle_id)

            if vehicle is None:
                return None

            return self.parking_lot.copy_vehicles([vehicle])[0]

    # --------------------------------------------------
    # INICIAR SIMULACIÓN
    # --------------------------------------------------

    def start_simulation(self) -> bool:
        """
        Inicia la simulación y, si está habilitada,
        activa la generación automática de vehículos.

        Devuelve False si ya estaba en ejecución.
        Lanza RuntimeError si aún quedan hilos de la ejecución
        anterior (usa is_idle para comprobarlo antes).
        """

        with self._lock:

            if self._running:
                return False

            # No reactivamos el Event mientras quedan hilos
            # de la ejecución anterior.
            if self._active_thread_names_unlocked():
                raise RuntimeError(
                    "Aún hay hilos de la simulación anterior "
                    "en ejecución. Espera a que terminen "
                    "o utiliza reset_simulation()."
                )

            self._simulation_stop_event.clear()
            self._running = True

            self._publish_event(
                "simulation_started",
                auto_generation=AUTO_GENERATION_ENABLED,
            )

            if AUTO_GENERATION_ENABLED:
                self._generation_thread = Thread(
                    target=self._arrival_loop,
                    name="vehicle-generator",
                    daemon=True,
                )

                self._generation_thread.start()

            else:
                self._generation_thread = None

            return True

    # --------------------------------------------------
    # CREAR VEHÍCULOS
    # --------------------------------------------------

    def add_vehicle(
        self,
        arrival_delay: float | None = None,
    ) -> Vehicle:
        """
        Crea un vehículo, registra su trabajador e inicia su hilo.

        arrival_delay: retraso antes de solicitar acceso. Si es None
        se sortea dentro del rango configurado.

        Solo puede utilizarse mientras la simulación
        admita nuevas llegadas.
        Devuelve una copia del vehículo creado.
        """

        if arrival_delay is not None and arrival_delay < 0:
            raise ValueError(
                "arrival_delay no puede ser negativo."
            )

        with self._lock:

            if (
                not self._running
                or self._simulation_stop_event.is_set()
            ):
                raise RuntimeError(
                    "No se pueden agregar vehículos "
                    "si la simulación está detenida."
                )

            # Generación de identificadores: V001, V002...
            self._vehicle_counter += 1

            vehicle_id = f"V{self._vehicle_counter:03d}"

            # Cada vehículo recibe tiempos independientes.
            if arrival_delay is None:
                arrival_delay = self._rng.uniform(
                    VEHICLE_ARRIVAL_DELAY_MIN_SECONDS,
                    VEHICLE_ARRIVAL_DELAY_MAX_SECONDS,
                )

            parking_duration = self._rng.uniform(
                PARKING_DURATION_MIN_SECONDS,
                PARKING_DURATION_MAX_SECONDS,
            )

            vehicle = Vehicle(
                vehicle_id=vehicle_id,
                arrival_delay=arrival_delay,
                parking_duration=parking_duration,
            )

            # Un trabajador por vehículo.
            worker = VehicleWorker(
                vehicle=vehicle,
                parking_lot=self.parking_lot,
                event_queue=self.event_queue,
                simulation_stop_event=self._simulation_stop_event,
            )

            # Un hilo independiente por vehículo. Es daemon para que
            # cerrar la aplicación nunca quede bloqueado por ellos.
            thread = Thread(
                target=worker.run,
                name=f"vehicle-{vehicle_id}",
                daemon=True,
            )

            # Registramos todo antes de iniciar el hilo.
            self._vehicles[vehicle_id] = vehicle
            self._workers[vehicle_id] = worker
            self._threads[vehicle_id] = thread

            self._publish_event(
                "vehicle_created",
                vehicle_id=vehicle_id,
                arrival_delay=arrival_delay,
                parking_duration=parking_duration,
            )

            try:
                thread.start()

            except Exception:
                # Si no pudo iniciarse, retiramos el registro
                # para no conservar un vehículo inexistente
                # en la ejecución.
                self._vehicles.pop(vehicle_id, None)
                self._workers.pop(vehicle_id, None)
                self._threads.pop(vehicle_id, None)
                self._vehicle_counter -= 1
                raise

            return replace(vehicle)

    def add_manual_vehicle(self) -> Vehicle:
        """
        Crea un vehículo para el botón "Agregar vehículo".

        Usa un retraso de llegada corto para que la acción del
        usuario se vea casi de inmediato.
        """

        with self._lock:
            delay = self._rng.uniform(
                MANUAL_VEHICLE_ARRIVAL_DELAY_MIN_SECONDS,
                MANUAL_VEHICLE_ARRIVAL_DELAY_MAX_SECONDS,
            )

        return self.add_vehicle(arrival_delay=delay)

    # --------------------------------------------------
    # GENERACIÓN AUTOMÁTICA
    # --------------------------------------------------

    def _arrival_loop(self) -> None:
        """
        Genera vehículos automáticamente.

        La primera llegada se genera inmediatamente.
        Las siguientes respetan un intervalo aleatorio.
        """

        try:
            while not self._simulation_stop_event.is_set():

                try:
                    self.add_vehicle()

                except RuntimeError:
                    # Si se detuvo mientras intentábamos
                    # generar un vehículo, salimos normalmente.
                    if self._simulation_stop_event.is_set():
                        break

                    raise

                with self._lock:
                    interval = self._rng.uniform(
                        AUTO_ARRIVAL_INTERVAL_MIN_SECONDS,
                        AUTO_ARRIVAL_INTERVAL_MAX_SECONDS,
                    )

                # Espera interrumpible: no utilizamos sleep().
                if self._simulation_stop_event.wait(interval):
                    break

        except Exception as error:
            self._publish_event(
                "controller_error",
                error=f"{type(error).__name__}: {error}",
            )

            self.stop_simulation()

        finally:
            self._publish_event("generation_stopped")

    # --------------------------------------------------
    # DETENER SIMULACIÓN
    # --------------------------------------------------

    def _request_all_exits(self, reason: str) -> None:
        """
        Pide la salida de todos los vehículos estacionados.

        Los que aún no están estacionados ignoran la petición; ellos
        se cancelan por el Event de parada. Debe llamarse DESPUÉS de
        activar _simulation_stop_event.
        """

        with self._lock:
            workers = tuple(self._workers.values())

        for worker in workers:
            worker.request_exit(reason)

    def stop_simulation(self) -> None:
        """
        Detiene las llegadas automáticas, cancela las solicitudes
        que todavía esperan acceso y pide la salida de los
        vehículos estacionados.

        Los hilos terminan por sí solos en poco tiempo; usa is_idle
        para saber cuándo ya no queda ninguno activo.
        """

        with self._lock:

            was_running = self._running

            self._running = False
            self._simulation_stop_event.set()

            if was_running:
                self._publish_event("simulation_stopping")

        # Orden: primero el Event de parada, luego las salidas.
        self._request_all_exits("simulation_stopped")

    # --------------------------------------------------
    # SOLICITAR SALIDA MANUAL
    # --------------------------------------------------

    def request_vehicle_exit(self, vehicle_id: str) -> None:
        """
        Solicita que un vehículo estacionado salga antes
        de completar su permanencia prevista.

        Lanza ValueError si el vehículo no existe o no está
        disponible para una salida manual.
        """

        with self._lock:
            worker = self._workers.get(vehicle_id)

        if worker is None:
            raise ValueError(
                f"No existe el vehículo {vehicle_id}."
            )

        # El trabajador solo acepta la petición si el vehículo
        # está estacionado y no tiene otra salida pendiente.
        # El controlador no libera recursos: lo hace el trabajador.
        if not worker.request_exit("manual_exit"):
            raise ValueError(
                f"El vehículo {vehicle_id} no está "
                "disponible para una salida manual."
            )

        self._publish_event(
            "vehicle_exit_requested",
            vehicle_id=vehicle_id,
        )

    # --------------------------------------------------
    # FINALIZACIÓN CONTROLADA DE LOS HILOS
    # --------------------------------------------------

    def _terminate_workers(self, timeout: float) -> None:
        """
        Solicita la finalización de los trabajadores y
        espera hasta timeout segundos en total.
        """

        if timeout <= 0:
            raise ValueError(
                "timeout debe ser mayor que cero."
            )

        with self._lock:
            threads = tuple(self._threads.values())
            generation_thread = self._generation_thread

        # Solicitamos la salida anticipada a los vehículos
        # estacionados. Los que esperan son cancelados
        # mediante _simulation_stop_event.
        self._request_all_exits("simulation_stopped")

        all_threads = list(threads)

        if generation_thread is not None:
            all_threads.append(generation_thread)

        deadline = monotonic() + timeout

        for thread in all_threads:
            if thread.ident is not None and thread.is_alive():
                remaining = max(
                    0.0,
                    deadline - monotonic(),
                )

                thread.join(timeout=remaining)

        active_threads = [
            thread.name
            for thread in all_threads
            if thread.is_alive()
        ]

        if active_threads:
            raise TimeoutError(
                "No terminaron todos los hilos dentro del "
                f"tiempo permitido: {active_threads}"
            )

        # Comprobamos que los trabajadores hayan liberado
        # correctamente los recursos compartidos.
        snapshot = self.parking_lot.get_snapshot()

        if (
            snapshot["occupied_count"] != 0
            or snapshot["waiting_count"] != 0
        ):
            raise RuntimeError(
                "No se puede completar la operación: "
                "quedaron vehículos ocupando o esperando "
                "espacios después de finalizar los hilos."
            )

    # --------------------------------------------------
    # REINICIAR SIMULACIÓN
    # --------------------------------------------------

    def reset_simulation(self, timeout: float = 5.0) -> None:
        """
        Detiene la simulación, solicita la salida de todos
        los vehículos y limpia el estado cuando sus hilos
        han finalizado correctamente.

        Puede lanzar TimeoutError o RuntimeError: la interfaz debe
        capturarlas y mostrar una alerta.
        """

        self.stop_simulation()

        # No limpiamos registros mientras los hilos siguen
        # modificando el parqueadero.
        self._terminate_workers(timeout)

        with self._lock:
            self._vehicles.clear()
            self._workers.clear()
            self._threads.clear()

            self._vehicle_counter = 0
            self._generation_thread = None

            # Misma semilla en cada ejecución: experimentos
            # comparables entre sí.
            self._rng = Random(self._seed)

        self._publish_event("simulation_reset")

    # --------------------------------------------------
    # CIERRE DE LA APLICACIÓN
    # --------------------------------------------------

    def shutdown(self, timeout: float = 5.0) -> bool:
        """
        Detiene la simulación y permite terminar
        ordenadamente sus hilos antes de cerrar la app.

        Nunca lanza por hilos rezagados (son daemon y no impiden
        cerrar). Devuelve True si el cierre fue limpio.
        """

        self.stop_simulation()

        try:
            self._terminate_workers(timeout)
            clean = True

        except (TimeoutError, RuntimeError) as error:
            clean = False

            self._publish_event(
                "controller_error",
                error=f"{type(error).__name__}: {error}",
            )

        self._publish_event("controller_shutdown", clean=clean)

        return clean