from threading import Lock
from time import time


class Metrics:
    """
    Calcula métricas de la simulación a partir de los eventos.

    Se alimenta con consume(evento) desde el despachador único (hilo
    principal). Usa las marcas de tiempo de los eventos, no el reloj
    de la interfaz, así los resultados no dependen del refresco de la UI.

    - Ocupación y cola: se reconstruyen con conjuntos de vehicle_id.
    - Utilización = plaza-segundos ocupadas / (capacidad * duración).
      La duración va del primer evento al último (o a `now`). Si la
      simulación se detiene y se reanuda sin reiniciar, el tiempo
      detenido también cuenta; reiniciar pone todo en cero.
    """

    def __init__(self, capacity: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("La capacidad debe ser un entero mayor que cero.")

        self._capacity = capacity
        self._lock = Lock()
        self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self._created = 0
        self._served = 0
        self._cancelled = 0
        self._errors = 0
        self._exit_reasons: dict[str, int] = {}

        self._waiting: set[str] = set()
        self._parked: set[str] = set()
        self._max_queue = 0
        self._max_occupied = 0

        self._wait_sum = 0.0
        self._wait_count = 0
        self._wait_max = 0.0
        self._parking_sum = 0.0
        self._parking_count = 0

        self._start_ts: float | None = None
        self._last_ts: float | None = None
        self._area = 0.0  # plaza-segundos ocupadas

    @staticmethod
    def _number(value: object) -> float | None:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return number if number == number and number >= 0 else None

    def _advance_unlocked(self, ts: float) -> None:
        """Integra la ocupación hasta ts (nunca retrocede el reloj)."""
        if self._last_ts is None:
            self._start_ts = self._last_ts = ts
            return

        if ts > self._last_ts:
            self._area += len(self._parked) * (ts - self._last_ts)
            self._last_ts = ts

    def reset(self) -> None:
        with self._lock:
            self._reset_unlocked()

    def consume(self, event: dict[str, object]) -> None:
        etype = event.get("type")

        with self._lock:
            if etype == "simulation_reset":
                self._reset_unlocked()
                return

            raw_ts = self._number(event.get("timestamp"))
            self._advance_unlocked(raw_ts if raw_ts is not None else time())

            vid = event.get("vehicle_id")
            vid = None if vid is None else str(vid)

            if etype == "vehicle_created":
                self._created += 1

            elif etype == "vehicle_waiting" and vid:
                self._waiting.add(vid)
                self._max_queue = max(self._max_queue, len(self._waiting))

            elif etype == "vehicle_parked" and vid:
                self._waiting.discard(vid)
                self._parked.add(vid)
                self._max_occupied = max(self._max_occupied, len(self._parked))

                wait = self._number(event.get("waiting_time"))
                if wait is not None:
                    self._wait_sum += wait
                    self._wait_count += 1
                    self._wait_max = max(self._wait_max, wait)

            elif etype == "vehicle_finished" and vid:
                self._parked.discard(vid)
                self._served += 1

                reason = str(event.get("reason") or "desconocido")
                self._exit_reasons[reason] = self._exit_reasons.get(reason, 0) + 1

                parking = self._number(event.get("parking_time"))
                if parking is not None:
                    self._parking_sum += parking
                    self._parking_count += 1

            elif etype == "vehicle_cancelled" and vid:
                self._waiting.discard(vid)
                self._cancelled += 1

            elif etype in ("vehicle_error", "controller_error"):
                self._errors += 1

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        """Copia de las métricas. `now` extiende la duración hasta ese instante."""
        with self._lock:
            area = self._area
            duration = 0.0

            if self._start_ts is not None and self._last_ts is not None:
                end = self._last_ts if now is None else max(now, self._last_ts)
                area += len(self._parked) * (end - self._last_ts)
                duration = end - self._start_ts

            capacity_seconds = self._capacity * duration

            return {
                "capacity": self._capacity,
                "created": self._created,
                "served": self._served,
                "cancelled": self._cancelled,
                "errors": self._errors,
                "exit_reasons": dict(self._exit_reasons),
                "waiting_now": len(self._waiting),
                "occupied_now": len(self._parked),
                "max_occupied": self._max_occupied,
                "max_queue": self._max_queue,
                "avg_wait": self._wait_sum / self._wait_count if self._wait_count else 0.0,
                "max_wait": self._wait_max,
                "avg_parking": (
                    self._parking_sum / self._parking_count if self._parking_count else 0.0
                ),
                "utilization": min(1.0, area / capacity_seconds) if capacity_seconds > 0 else 0.0,
                "duration": duration,
            }