
from queue import Queue
from random import uniform
from threading import Event, Lock, Thread
from time import monotonic, time

from config import (
    AUTO_ARRIVAL_INTERVAL_MAX_SECONDS,
    AUTO_ARRIVAL_INTERVAL_MIN_SECONDS,
    AUTO_GENERATION_ENABLED,
    PARKING_CAPACITY,
    PARKING_DURATION_MAX_SECONDS,
    PARKING_DURATION_MIN_SECONDS,
    VEHICLE_ARRIVAL_DELAY_MAX_SECONDS,
    VEHICLE_ARRIVAL_DELAY_MIN_SECONDS,
)

from core.parking import ParkingLot
from models.vehicle import Vehicle, VehicleStatus
from threads.vehicle_worker import VehicleWorker


class SimulationController:
    """
    Coordina la creación de vehículos, los hilos y
    el inicio, la detención y el reinicio del simulador.
    """

    def __init__(
        self,
        parking_lot: ParkingLot | None = None,
        event_queue: Queue[dict[str, object]] | None = None,
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

    @property
    def is_running(self) -> bool:
        """Indica si la simulación admite nuevas llegadas."""

        with self._lock:
            return self._running

    @property
    def vehicles(self) -> tuple[Vehicle, ...]:
        """Devuelve una colección de los vehículos registrados."""

        with self._lock:
            return tuple(self._vehicles.values())

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        """Busca un vehículo por su identificador."""

        with self._lock:
            return self._vehicles.get(vehicle_id)

    # --------------------------------------------------
    # INICIAR SIMULACIÓN
    # --------------------------------------------------

    def start_simulation(self) -> bool:
        """
        Inicia la simulación y, si está habilitada,
        activa la generación automática de vehículos.

        Devuelve False si ya estaba en ejecución.
        """

        with self._lock:

            if self._running:
                return False

            # No reactivamos el Event mientras quedan hilos
            # de la ejecución anterior.
            active_threads = [
                thread.name
                for thread in self._threads.values()
                if thread.is_alive()
            ]

            if (
                self._generation_thread is not None
                and self._generation_thread.is_alive()
            ):
                active_threads.append(
                    self._generation_thread.name
                )

            if active_threads:
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
                )

                self._generation_thread.start()

            else:
                self._generation_thread = None

            return True

    # --------------------------------------------------
    # CREAR VEHÍCULOS
    # --------------------------------------------------

    def add_vehicle(self) -> Vehicle:
        """
        Crea un vehículo, registra su trabajador e inicia su hilo.

        Solo puede utilizarse mientras la simulación
        admita nuevas llegadas.
        """

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
            arrival_delay = uniform(
                VEHICLE_ARRIVAL_DELAY_MIN_SECONDS,
                VEHICLE_ARRIVAL_DELAY_MAX_SECONDS,
            )

            parking_duration = uniform(
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

            # Un hilo independiente por vehículo.
            thread = Thread(
                target=worker.run,
                name=f"vehicle-{vehicle_id}",
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

            return vehicle

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

                interval = uniform(
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

    def stop_simulation(self) -> None:
        """
        Detiene las llegadas automáticas y cancela las
        solicitudes que todavía esperan acceso.

        Los vehículos ya estacionados pueden finalizar
        su permanencia normal.
        """

        with self._lock:

            was_running = self._running

            self._running = False
            self._simulation_stop_event.set()

            if was_running:
                self._publish_event("simulation_stopping")

    # --------------------------------------------------
    # SOLICITAR SALIDA MANUAL
    # --------------------------------------------------

    def request_vehicle_exit(self, vehicle_id: str) -> None:
        """
        Solicita que un vehículo estacionado salga antes
        de completar su permanencia prevista.
        """

        with self._lock:
            worker = self._workers.get(vehicle_id)
            vehicle = self._vehicles.get(vehicle_id)

        if worker is None or vehicle is None:
            raise ValueError(
                f"No existe el vehículo {vehicle_id}."
            )

        # Verificamos que el vehículo ocupe actualmente
        # una plaza en el parqueadero.
        parked_ids = {
            parked.vehicle_id
            for parked in self.parking_lot.parked_vehicles
        }

        if (
            vehicle_id not in parked_ids
            or vehicle.status != VehicleStatus.PARKED
        ):
            raise ValueError(
                f"El vehículo {vehicle_id} no está "
                "disponible para una salida manual."
            )

        # El trabajador liberará la plaza cuando atienda
        # la señal. El controlador no libera recursos.
        worker.request_exit()

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
            workers = tuple(self._workers.values())
            threads = tuple(self._threads.values())
            generation_thread = self._generation_thread

        # Solicitamos la salida anticipada a los vehículos
        # estacionados. Los que esperan son cancelados
        # mediante _simulation_stop_event.
        for worker in workers:
            worker.request_exit()

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

        self._publish_event("simulation_reset")

    # --------------------------------------------------
    # CIERRE DE LA APLICACIÓN
    # --------------------------------------------------

    def shutdown(self, timeout: float = 5.0) -> None:
        """
        Detiene la simulación y permite terminar
        ordenadamente sus hilos antes de cerrar la app.
        """

        self.stop_simulation()
        self._terminate_workers(timeout)

        self._publish_event("controller_shutdown")
