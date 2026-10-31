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

    Mecanismos de sincronización que usa:
    - simulation_stop_event (Event global): cancela la espera de
      llegada y la espera de plaza.
    - _exit_event (Event propio): termina antes la permanencia, ya sea
      por una salida manual o por la parada de la simulación.
    - ParkingLot: Semaphore + Lock (internos).
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
        # de este vehículo, y el motivo que quedará registrado.
        self._exit_event = Event()
        self._exit_reason = "manual_exit"

    def request_exit(self, reason: str = "manual_exit") -> bool:
        """
        Solicita la salida anticipada del vehículo.

        Solo se acepta si el vehículo está estacionado, para que una
        petición tardía no haga salir al vehículo apenas ingrese.
        Devuelve True si la petición fue aceptada.

        El controlador usa reason="simulation_stopped" al detener o
        reiniciar la simulación, para que los vehículos estacionados
        no tengan que esperar su permanencia completa.
        """
        if self.vehicle.status != VehicleStatus.PARKED:
            return False

        if self._exit_event.is_set():
            # Ya hay una petición previa; se conserva su motivo.
            return False

        self._exit_reason = reason
        self._exit_event.set()
        return True

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
                waiting_started=waiting_started,
            )

            # Marca de inicio inmediatamente después de obtener la
            # plaza: así, pase lo que pase a partir de aquí, el
            # bloque finally sabe que debe liberarla.
            parking_started = monotonic()

            # waiting_time lo registra ParkingLot bajo su Lock (tiempo
            # real hasta conseguir plaza; no incluye arrival_delay).

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
            self._publish_event(
                "vehicle_parked",
                status=self.vehicle.status.value,
                parking_space=space_number,
                waiting_time=round(
                    self.vehicle.waiting_time, 2
                ),
                parking_duration=self.vehicle.parking_duration,
            )

            # 5. Permanece estacionado hasta que se cumpla su tiempo
            #    o llegue una solicitud de salida.
            if self.simulation_stop_event.is_set():
                # La parada llegó justo cuando ingresaba: no espera.
                exit_reason = "simulation_stopped"
            elif self._exit_event.wait(
                timeout=self.vehicle.parking_duration
            ):
                exit_reason = self._exit_reason

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
            if space_number is not None:

                parking_time = (
                    0.0
                    if parking_started is None
                    else monotonic() - parking_started
                )

                try:
                    # 6. Transición al estado de salida; parking_time
                    #    se guarda en el vehículo bajo el Lock del lote.
                    self.parking_lot.mark_exiting(
                        self.vehicle.vehicle_id,
                        parking_time=parking_time,
                    )

                    self._publish_event(
                        "vehicle_exiting",
                        status=VehicleStatus.EXITING.value,
                        parking_space=space_number,
                        parking_time=round(parking_time, 2),
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
                        parking_time=round(parking_time, 2),
                        reason=exit_reason,
                    )