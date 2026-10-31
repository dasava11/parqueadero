from collections import deque
from dataclasses import replace
from threading import BoundedSemaphore, Event, Lock
from time import monotonic, sleep

from config import (
    PARKING_CAPACITY,
    PARKING_ACQUIRE_POLL_INTERVAL_SECONDS,
)
from models.vehicle import Vehicle, VehicleStatus


class ParkingLot:
    """
    Administra los espacios físicos y los recursos compartidos
    del parqueadero.

    Mecanismos de sincronización:
    - BoundedSemaphore: cuenta los permisos (plazas libres).
    - Lock: protege plazas, cola de espera y estados (exclusión mutua).
    - Event (externo): permite cancelar las esperas.

    La cola de espera sigue una política FIFO:
    el primer vehículo que llega es el primero en obtener una plaza.

    Las propiedades de consulta y get_snapshot() devuelven COPIAS de
    los vehículos, para que la interfaz y las métricas nunca lean
    objetos que otros hilos estén modificando.
    """

    def __init__(self, capacity: int = PARKING_CAPACITY) -> None:
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
        ):
            raise ValueError(
                "La capacidad debe ser un entero mayor que cero."
            )

        self.capacity = capacity

        # Cada posición representa un espacio físico.
        # None significa que el espacio está libre.
        self._spaces: list[Vehicle | None] = [None] * capacity

        # Cola FIFO de vehículos que esperan una plaza.
        self._waiting_vehicles: deque[Vehicle] = deque()

        # Controla el número de plazas disponibles.
        self._space_semaphore = BoundedSemaphore(capacity)

        # Protege las estructuras compartidas.
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Utilidades internas (se ejecutan con self._lock ya adquirido)
    # ------------------------------------------------------------------

    @staticmethod
    def _copy(vehicle: Vehicle | None) -> Vehicle | None:
        """Devuelve una copia independiente de un vehículo."""
        return None if vehicle is None else replace(vehicle)

    def _find_parked_unlocked(self, vehicle_id: str) -> int | None:
        """Devuelve el índice de la plaza de un vehículo, o None."""
        for index, vehicle in enumerate(self._spaces):
            if vehicle is not None and vehicle.vehicle_id == vehicle_id:
                return index
        return None

    def _remove_waiting_vehicle_unlocked(self, vehicle_id: str) -> None:
        """Elimina un vehículo de la cola de espera."""
        self._waiting_vehicles = deque(
            vehicle
            for vehicle in self._waiting_vehicles
            if vehicle.vehicle_id != vehicle_id
        )

    # ------------------------------------------------------------------
    # Operaciones principales
    # ------------------------------------------------------------------

    def occupy_space(
        self,
        vehicle: Vehicle,
        stop_event: Event | None = None,
        poll_interval: float = PARKING_ACQUIRE_POLL_INTERVAL_SECONDS,
        waiting_started: float | None = None,
    ) -> int | None:
        """
        Espera una plaza y asigna un espacio al vehículo.

        Respeta el orden FIFO de la cola.

        Devuelve el número de la plaza asignada (1..capacidad).
        Devuelve None si la operación se cancela.
        """
        if poll_interval <= 0:
            raise ValueError("poll_interval debe ser mayor que cero.")

        vehicle_id = vehicle.vehicle_id

        # 1. Registro del vehículo en la cola de espera.
        with self._lock:
            already_waiting = any(
                waiting.vehicle_id == vehicle_id
                for waiting in self._waiting_vehicles
            )

            if (
                self._find_parked_unlocked(vehicle_id) is not None
                or already_waiting
            ):
                raise ValueError(
                    f"El vehículo {vehicle_id} ya está registrado."
                )

            if vehicle.status not in (
                VehicleStatus.GENERATED,
                VehicleStatus.APPROACHING,
            ):
                raise ValueError(
                    f"El vehículo {vehicle_id} no puede ingresar "
                    f"desde el estado {vehicle.status.value}."
                )

            if stop_event is not None and stop_event.is_set():
                vehicle.status = VehicleStatus.CANCELLED
                return None

            self._waiting_vehicles.append(vehicle)
            vehicle.status = VehicleStatus.WAITING

        acquired = False  # ¿tenemos un permiso que aún no se usó?
        assigned = False  # ¿ya se asignó una plaza?

        try:
            # 2. Espera con pausas: nunca es un bucle sin descanso.
            while True:
                if stop_event is not None and stop_event.is_set():
                    return None

                with self._lock:
                    # Solo el primer vehículo de la cola puede
                    # intentar obtener la siguiente plaza (FIFO).
                    is_first = (
                        bool(self._waiting_vehicles)
                        and self._waiting_vehicles[0].vehicle_id
                        == vehicle_id
                    )

                    if is_first and self._space_semaphore.acquire(
                        blocking=False
                    ):
                        acquired = True

                        # La cancelación pudo ocurrir justo
                        # después de la comprobación anterior.
                        if stop_event is not None and stop_event.is_set():
                            self._space_semaphore.release()
                            acquired = False
                            return None

                        try:
                            space_index = self._spaces.index(None)
                        except ValueError as exc:
                            raise RuntimeError(
                                "Se obtuvo un permiso, pero no "
                                "hay ningún espacio libre."
                            ) from exc

                        # Asignación atómica mientras el Lock
                        # protege las estructuras.
                        self._spaces[space_index] = vehicle
                        self._waiting_vehicles.popleft()

                        vehicle.parking_space = space_index + 1
                        vehicle.status = VehicleStatus.PARKED

                        # Mismo Lock que el cambio de estado: una copia
                        # nunca ve PARKED con la espera sin registrar.
                        if waiting_started is not None:
                            vehicle.waiting_time = (
                                monotonic() - waiting_started
                            )

                        acquired = False
                        assigned = True

                        return space_index + 1

                # Pausa fuera del Lock, cancelable con el Event.
                if stop_event is not None:
                    if stop_event.wait(poll_interval):
                        return None
                else:
                    sleep(poll_interval)

        finally:
            # Si no se asignó plaza (cancelación o error), se devuelve
            # el permiso pendiente y se limpia la cola de espera.
            with self._lock:
                if acquired:
                    self._space_semaphore.release()

                if not assigned:
                    self._remove_waiting_vehicle_unlocked(vehicle_id)

                    if waiting_started is not None:
                        vehicle.waiting_time = (
                            monotonic() - waiting_started
                        )

                    if vehicle.status == VehicleStatus.WAITING:
                        vehicle.status = VehicleStatus.CANCELLED

    def release_space(self, vehicle_id: str) -> int:
        """
        Libera la plaza ocupada por un vehículo y devuelve el permiso
        al semáforo. Devuelve el número de la plaza liberada.
        """
        with self._lock:
            index = self._find_parked_unlocked(vehicle_id)

            if index is None:
                raise ValueError(
                    f"El vehículo {vehicle_id} no ocupa ningún espacio."
                )

            vehicle = self._spaces[index]
            self._spaces[index] = None

            vehicle.parking_space = None
            vehicle.status = VehicleStatus.FINISHED

            self._space_semaphore.release()

            return index + 1

    def mark_exiting(
        self, vehicle_id: str, parking_time: float | None = None
    ) -> None:
        """Marca como saliente un vehículo estacionado."""
        with self._lock:
            index = self._find_parked_unlocked(vehicle_id)

            if index is None:
                raise ValueError(
                    f"El vehículo {vehicle_id} no está estacionado."
                )

            vehicle = self._spaces[index]

            if parking_time is not None:
                vehicle.parking_time = parking_time

            vehicle.status = VehicleStatus.EXITING

    # ------------------------------------------------------------------
    # Consultas (devuelven copias)
    # ------------------------------------------------------------------

    @property
    def spaces(self) -> tuple[Vehicle | None, ...]:
        """Copia de las plazas del parqueadero."""
        with self._lock:
            return tuple(self._copy(v) for v in self._spaces)

    @property
    def waiting_vehicles(self) -> tuple[Vehicle, ...]:
        """Copia de los vehículos que esperan, en orden FIFO."""
        with self._lock:
            return tuple(self._copy(v) for v in self._waiting_vehicles)

    @property
    def parked_vehicles(self) -> tuple[Vehicle, ...]:
        """Copia de los vehículos actualmente estacionados."""
        with self._lock:
            return tuple(
                self._copy(v) for v in self._spaces if v is not None
            )

    @property
    def occupied_count(self) -> int:
        """Número de plazas ocupadas."""
        with self._lock:
            return sum(v is not None for v in self._spaces)

    @property
    def available_count(self) -> int:
        """Número de plazas libres."""
        with self._lock:
            return sum(v is None for v in self._spaces)

    @property
    def waiting_count(self) -> int:
        """Número de vehículos en espera."""
        with self._lock:
            return len(self._waiting_vehicles)

    @property
    def is_full(self) -> bool:
        """Indica si el parqueadero está lleno."""
        with self._lock:
            return all(v is not None for v in self._spaces)

    def get_snapshot(self) -> dict:
        """
        Fotografía coherente del parqueadero, tomada con un único
        Lock. Contiene copias: es seguro leerla desde la interfaz.
        """
        with self._lock:
            spaces = tuple(self._copy(v) for v in self._spaces)
            waiting = tuple(self._copy(v) for v in self._waiting_vehicles)

            parked = tuple(v for v in spaces if v is not None)
            occupied = len(parked)

            return {
                "capacity": self.capacity,
                "spaces": spaces,
                "waiting_vehicles": waiting,
                "parked_vehicles": parked,
                "occupied_count": occupied,
                "available_count": self.capacity - occupied,
                "waiting_count": len(waiting),
                "is_full": occupied == self.capacity,
            }

    def copy_vehicles(self, vehicles) -> tuple[Vehicle, ...]:
        """Copias tomadas con el Lock que protege estados y tiempos."""
        with self._lock:
            return tuple(self._copy(v) for v in vehicles)
    