from config import (
    PARKING_CAPACITY,
    PARKING_ACQUIRE_POLL_INTERVAL_SECONDS,
)

from collections import deque
from threading import BoundedSemaphore, Event, Lock
from time import sleep

from models.vehicle import Vehicle, VehicleStatus


class ParkingLot:
    """
    Administra los espacios físicos y los recursos compartidos
    del parqueadero.

    La cola de espera sigue una política FIFO:
    el primer vehículo que llega es el primero en obtener una plaza.
    """

    def __init__(
    self,
    capacity: int = PARKING_CAPACITY
) -> None:
        if capacity <= 0:
            raise ValueError("La capacidad debe ser mayor que cero.")

        self.capacity = capacity

        # Cada posición representa un espacio físico.
        # None significa que el espacio está libre.
        self._spaces: list[Vehicle | None] = [
            None for _ in range(capacity)
        ]

        # Cola FIFO de vehículos que esperan una plaza.
        self._waiting_vehicles: deque[Vehicle] = deque()

        # Controla el número de plazas disponibles.
        self._space_semaphore = BoundedSemaphore(capacity)

        # Protege las estructuras compartidas.
        self._lock = Lock()

    def _remove_waiting_vehicle_unlocked(
        self,
        vehicle_id: str,
    ) -> None:
        """
        Elimina un vehículo de la cola.

        Debe ejecutarse mientras se posee self._lock.
        """
        self._waiting_vehicles = deque(
            vehicle
            for vehicle in self._waiting_vehicles
            if vehicle.vehicle_id != vehicle_id
        )

    def occupy_space(
        self,
        vehicle: Vehicle,
        stop_event: Event | None = None,
        poll_interval: float = PARKING_ACQUIRE_POLL_INTERVAL_SECONDS,
    ) -> int | None:
        """
        Espera una plaza y asigna un espacio al vehículo.

        Respeta el orden FIFO de la cola.

        Devuelve el número de la plaza asignada.
        Devuelve None si la operación se cancela.
        """
        if poll_interval <= 0:
            raise ValueError(
                "poll_interval debe ser mayor que cero."
            )

        vehicle_id = vehicle.vehicle_id

        # Registramos al vehículo en la cola.
        with self._lock:
            already_parked = any(
                parked is not None
                and parked.vehicle_id == vehicle_id
                for parked in self._spaces
            )

            already_waiting = any(
                waiting.vehicle_id == vehicle_id
                for waiting in self._waiting_vehicles
            )

            if already_parked or already_waiting:
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

        acquired = False
        assigned = False

        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return None

                with self._lock:
                    # Solo el primer vehículo de la cola puede
                    # intentar obtener la siguiente plaza.
                    is_first = (
                        bool(self._waiting_vehicles)
                        and self._waiting_vehicles[0].vehicle_id
                        == vehicle_id
                    )

                    if (
                        is_first
                        and stop_event is not None
                        and stop_event.is_set()
                    ):
                        return None

                    if (
                        is_first
                        and self._space_semaphore.acquire(
                            blocking=False
                        )
                    ):
                        acquired = True

                        # La cancelación pudo ocurrir justo
                        # después de comprobar el evento.
                        if (
                            stop_event is not None
                            and stop_event.is_set()
                        ):
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

                        # Asignamos la plaza mientras el Lock
                        # sigue protegiendo las estructuras.
                        self._spaces[space_index] = vehicle

                        self._waiting_vehicles.popleft()

                        vehicle.parking_space = space_index + 1
                        vehicle.status = VehicleStatus.PARKED

                        acquired = False
                        assigned = True

                        return space_index + 1

                # Esperamos sin bloquear indefinidamente el hilo.
                if stop_event is not None:
                    if stop_event.wait(poll_interval):
                        return None
                else:
                    sleep(poll_interval)

        finally:
            # Si adquirimos un permiso pero no asignamos una plaza,
            # debemos devolverlo.
            with self._lock:
                if acquired:
                    self._space_semaphore.release()

                if not assigned:
                    self._remove_waiting_vehicle_unlocked(
                        vehicle_id
                    )

                    if vehicle.status == VehicleStatus.WAITING:
                        vehicle.status = VehicleStatus.CANCELLED

    def release_space(self, vehicle_id: str) -> int:
        """
        Libera la plaza ocupada por un vehículo.

        Devuelve el número de la plaza liberada.
        """
        with self._lock:
            for index, vehicle in enumerate(self._spaces):
                if (
                    vehicle is not None
                    and vehicle.vehicle_id == vehicle_id
                ):
                    self._spaces[index] = None

                    vehicle.parking_space = None
                    vehicle.status = VehicleStatus.FINISHED

                    # Devolvemos el permiso de la plaza liberada.
                    self._space_semaphore.release()

                    return index + 1

            raise ValueError(
                f"El vehículo {vehicle_id} no ocupa "
                "ningún espacio."
            )

    def mark_exiting(self, vehicle_id: str) -> None:
        """Marca como saliente un vehículo estacionado."""
        with self._lock:
            for vehicle in self._spaces:
                if (
                    vehicle is not None
                    and vehicle.vehicle_id == vehicle_id
                ):
                    vehicle.status = VehicleStatus.EXITING
                    return

        raise ValueError(
            f"El vehículo {vehicle_id} no está estacionado."
        )

    @property
    def spaces(self) -> tuple[Vehicle | None, ...]:
        """Devuelve una tupla con las plazas del parqueadero."""
        with self._lock:
            return tuple(self._spaces)

    @property
    def waiting_vehicles(self) -> tuple[Vehicle, ...]:
        """Devuelve los vehículos que están esperando."""
        with self._lock:
            return tuple(self._waiting_vehicles)

    @property
    def parked_vehicles(self) -> tuple[Vehicle, ...]:
        """Devuelve los vehículos actualmente estacionados."""
        with self._lock:
            return tuple(
                vehicle
                for vehicle in self._spaces
                if vehicle is not None
            )

    @property
    def occupied_count(self) -> int:
        """Devuelve el número de plazas ocupadas."""
        with self._lock:
            return sum(
                vehicle is not None
                for vehicle in self._spaces
            )

    @property
    def available_count(self) -> int:
        """Devuelve el número de plazas libres."""
        with self._lock:
            return self.capacity - sum(
                vehicle is not None
                for vehicle in self._spaces
            )

    @property
    def waiting_count(self) -> int:
        """Devuelve el número de vehículos en espera."""
        with self._lock:
            return len(self._waiting_vehicles)

    @property
    def is_full(self) -> bool:
        """Indica si el parqueadero está lleno."""
        with self._lock:
            return all(
                vehicle is not None
                for vehicle in self._spaces
            )

    def get_snapshot(self) -> dict:
        """
        Devuelve una fotografía coherente del parqueadero.
        """
        with self._lock:
            spaces = tuple(self._spaces)
            waiting = tuple(self._waiting_vehicles)

            parked = tuple(
                vehicle
                for vehicle in spaces
                if vehicle is not None
            )

            occupied = len(parked)

            return {
                "spaces": spaces,
                "waiting_vehicles": waiting,
                "parked_vehicles": parked,
                "occupied_count": occupied,
                "available_count": self.capacity - occupied,
                "waiting_count": len(waiting),
                "is_full": occupied == self.capacity,
            }
