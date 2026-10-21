
from queue import Queue
from threading import Event
from time import monotonic, time

from core.parking import ParkingLot
from models.vehicle import Vehicle, VehicleStatus


class VehicleWorker:
    """
    Ejecuta el ciclo de vida de un vehículo.

    No crea ni inicia el hilo directamente.
    El controlador será responsable de ejecutar run()
    dentro de un threading.Thread.
    """

    def __init__(
        self,
        vehicle: Vehicle,
        parking_lot: ParkingLot,
        event_queue: Queue[dict[str, object]],
        simulation_stop_event: Event,
    ) -> None:
        self.vehicle = vehicle
        self.parking_lot = parking_lot
        self.event_queue = event_queue
        self.simulation_stop_event = simulation_stop_event

        # Evento individual para solicitar la salida anticipada
        # de este vehículo.
        self._exit_event = Event()

    def request_exit(self) -> None:
        """
        Solicita la salida anticipada del vehículo.

        El controlador deberá permitir esta operación
        solamente cuando el vehículo esté estacionado.
        """
        self._exit_event.set()

    def _publish_event(
        self,
        event_type: str,
        **details: object,
    ) -> None:
        """
        Envía un evento a la cola de comunicación.
        No manipula widgets de Tkinter.
        """
        self.event_queue.put(
            {
                "type": event_type,
                "vehicle_id": self.vehicle.vehicle_id,
                "timestamp": time(),
                **details,
            }
        )

    def run(self) -> None:
        """
        Ejecuta el ciclo de vida completo del vehículo.
        """

        space_number: int | None = None
        parking_started: float | None = None

        exit_reason = "parking_duration_elapsed"

        try:
            # 1. El vehículo comienza su aproximación.
            self.vehicle.status = VehicleStatus.APPROACHING

            self._publish_event(
                "vehicle_approaching",
                status=self.vehicle.status.value,
                arrival_delay=self.vehicle.arrival_delay,
            )

            # 2. Espera el tiempo previsto antes de solicitar
            #    acceso. Event.wait permite cancelar esta espera.
            cancelled = self.simulation_stop_event.wait(
                timeout=self.vehicle.arrival_delay
            )

            if cancelled:
                self.vehicle.status = VehicleStatus.CANCELLED

                self._publish_event(
                    "vehicle_cancelled",
                    status=self.vehicle.status.value,
                    reason="simulation_stopped_before_arrival",
                )
                return

            # 3. Solicita acceso al parqueadero.
            waiting_started = monotonic()

            self._publish_event(
                "vehicle_waiting",
                status=VehicleStatus.WAITING.value,
            )

            space_number = self.parking_lot.occupy_space(
                vehicle=self.vehicle,
                stop_event=self.simulation_stop_event,
            )

            # Registra el tiempo real empleado en conseguir
            # una plaza. No incluye arrival_delay.
            self.vehicle.waiting_time = (
                monotonic() - waiting_started
            )

            # Si se canceló mientras esperaba, no estaciona.
            if space_number is None:
                self._publish_event(
                    "vehicle_cancelled",
                    status=self.vehicle.status.value,
                    waiting_time=round(
                        self.vehicle.waiting_time, 2
                    ),
                    reason="entry_cancelled",
                )
                return

            # 4. Comienza el estacionamiento.
            parking_started = monotonic()

            self._publish_event(
                "vehicle_parked",
                status=self.vehicle.status.value,
                parking_space=space_number,
                waiting_time=round(
                    self.vehicle.waiting_time, 2
                ),
                parking_duration=self.vehicle.parking_duration,
            )

            # 5. Espera su tiempo previsto o una solicitud
            #    de salida anticipada.
            manual_exit_requested = self._exit_event.wait(
                timeout=self.vehicle.parking_duration
            )

            if manual_exit_requested:
                exit_reason = "manual_exit"

        except Exception as error:
            self._publish_event(
                "vehicle_error",
                status=self.vehicle.status.value,
                error=f"{type(error).__name__}: {error}",
            )

            # Conservamos el error para facilitar la depuración.
            raise

        finally:
            # Si consiguió plaza, debe liberarla incluso si
            # ocurre un error durante su permanencia.
            if space_number is not None and parking_started is not None:

                self.vehicle.parking_time = (
                    monotonic() - parking_started
                )

                try:
                    # 6. Transición al estado de salida.
                    self.parking_lot.mark_exiting(
                        self.vehicle.vehicle_id
                    )

                    self._publish_event(
                        "vehicle_exiting",
                        status=VehicleStatus.EXITING.value,
                        parking_space=space_number,
                        parking_time=round(
                            self.vehicle.parking_time, 2
                        ),
                        reason=exit_reason,
                    )

                finally:
                    # 7. Libera el recurso compartido.
                    released_space = self.parking_lot.release_space(
                        self.vehicle.vehicle_id
                    )

                    self._publish_event(
                        "vehicle_finished",
                        status=VehicleStatus.FINISHED.value,
                        released_space=released_space,
                        waiting_time=round(
                            self.vehicle.waiting_time, 2
                        ),
                        parking_time=round(
                            self.vehicle.parking_time, 2
                        ),
                        reason=exit_reason,
                    )
