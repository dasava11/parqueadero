from collections.abc import Callable, Iterable
from queue import Empty, Queue


class EventDispatcher:
    """
    ÚNICO consumidor de la cola de eventos.

    Una queue.Queue reparte los eventos entre quienes llaman a get(),
    así que logger, métricas y vista no pueden leerla por separado.
    Este despachador retira cada evento una sola vez y lo reenvía a
    todos los manejadores, en orden.

    Llamar a drain() desde el bucle after() de Tkinter (hilo principal).
    Si un manejador falla, el error se guarda y los demás siguen
    recibiendo el evento: un fallo del logger no apaga las métricas.
    """

    MAX_ERRORS_KEPT = 50

    def __init__(
        self,
        event_queue: "Queue[dict[str, object]]",
        handlers: Iterable[Callable[[dict[str, object]], object]],
        max_events_per_tick: int = 200,
    ) -> None:
        if max_events_per_tick <= 0:
            raise ValueError("max_events_per_tick debe ser mayor que cero.")

        self._queue = event_queue
        self._handlers = tuple(handlers)
        self._max_per_tick = max_events_per_tick
        self._errors: list[str] = []
        self._error_count = 0

    @property
    def errors(self) -> tuple[str, ...]:
        """Últimos errores de manejadores (los más antiguos se descartan)."""
        return tuple(self._errors)

    @property
    def error_count(self) -> int:
        return self._error_count

    def drain(self, max_events: int | None = None) -> int:
        """
        Procesa hasta max_events eventos (por defecto max_events_per_tick,
        para no congelar la interfaz). Devuelve cuántos procesó.
        """
        limit = self._max_per_tick if max_events is None else max_events
        processed = 0

        while processed < limit:
            try:
                event = self._queue.get_nowait()
            except Empty:
                break

            processed += 1

            for handler in self._handlers:
                try:
                    handler(event)
                except Exception as error:  # un manejador nunca tumba al resto
                    self._error_count += 1
                    name = getattr(handler, "__qualname__", repr(handler))
                    self._errors.append(f"{name}: {type(error).__name__}: {error}")
                    del self._errors[: -self.MAX_ERRORS_KEPT]

        return processed

    def drain_all(self) -> int:
        """Vacía la cola por completo (cierre de la app y pruebas)."""
        total = 0
        while True:
            count = self.drain(self._max_per_tick)
            total += count
            if count == 0:
                return total