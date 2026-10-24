"""
Punto de entrada del simulador de parqueadero.

Ejecutar desde la raíz del proyecto:  python3 main.py

Este archivo solo CONECTA las piezas; no contiene lógica de simulación:

    SimulationController ──► event_queue ──► EventDispatcher ──┬─► EventLogger.log
    (workers y controller)   (productores)   (único consumidor)├─► Metrics.consume
                                                               └─► MainWindow.on_event

Contrato que debe cumplir ui/main_window.py:

    MainWindow(root, controller, metrics)
        Construye la interfaz. Los botones llaman directamente al controller:
        start_simulation, add_manual_vehicle, stop_simulation,
        reset_simulation y request_vehicle_exit. Debe capturar
        RuntimeError / ValueError / TimeoutError y mostrarlos como alerta,
        y habilitar "Iniciar" solo cuando controller.is_idle sea True.

    on_event(event: dict) -> None      (opcional)
        Se llama una vez por cada evento, en el hilo principal.

    refresh() -> None                  (opcional)
        Se llama una vez por ciclo, después de procesar los eventos:
        redibuja plazas, cola, contadores y métricas leyendo
        controller.parking_lot.get_snapshot(), controller.vehicles y
        metrics.snapshot().

Reglas: la interfaz solo se toca desde el hilo principal (bucle after);
los hilos de vehículos nunca ven la ventana.
"""

import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import UI_REFRESH_INTERVAL_MS
from core.controller import SimulationController
from services.dispatcher import EventDispatcher
from services.event_logger import EventLogger
from services.metrics import Metrics

SHUTDOWN_TIMEOUT_SECONDS = 5.0


@dataclass
class Backend:
    """Piezas de la simulación que no dependen de Tkinter."""

    controller: SimulationController
    metrics: Metrics
    logger: EventLogger


def build_backend(log_path: Path | str | None = None) -> Backend:
    """Crea controller, métricas y logger (por defecto, logs/events.csv)."""

    controller = SimulationController()

    return Backend(
        controller=controller,
        metrics=Metrics(controller.parking_lot.capacity),
        logger=EventLogger(log_path),
    )


class Application:
    """
    Une el backend con la ventana y gestiona el bucle after() y el cierre.

    `root` solo necesita after, after_cancel, protocol y destroy, así que
    se puede probar sin Tkinter con un objeto de reemplazo.
    """

    def __init__(
        self,
        root: Any,
        window_factory: Callable[[Any, SimulationController, Metrics], Any],
        backend: Backend | None = None,
    ) -> None:
        self.root = root
        self.backend = backend if backend is not None else build_backend()

        controller = self.backend.controller

        self.window = window_factory(
            root, controller, self.backend.metrics
        )

        # Orden: primero se registra y se calcula; la vista va al final.
        handlers: list[Callable[[dict[str, object]], object]] = [
            self.backend.logger.log,
            self.backend.metrics.consume,
        ]

        on_event = getattr(self.window, "on_event", None)
        if callable(on_event):
            handlers.append(on_event)

        # Único consumidor de la cola de eventos.
        self.dispatcher = EventDispatcher(controller.event_queue, handlers)

        self._after_id: Any = None
        self._closing = False
        self._last_refresh_error: str | None = None

        root.protocol("WM_DELETE_WINDOW", self.close)

    def start(self) -> None:
        """Programa el primer ciclo del bucle after()."""
        self._tick()

    def _tick(self) -> None:
        if self._closing:
            return

        try:
            self.dispatcher.drain()

            refresh = getattr(self.window, "refresh", None)
            if callable(refresh):
                refresh()

        except Exception as error:
            # Un fallo al dibujar no debe detener la simulación ni el bucle.
            message = f"{type(error).__name__}: {error}"
            if message != self._last_refresh_error:
                self._last_refresh_error = message
                traceback.print_exc(file=sys.stderr)

        finally:
            if not self._closing:
                self._after_id = self.root.after(
                    UI_REFRESH_INTERVAL_MS, self._tick
                )

    def close(self) -> None:
        """Cierre ordenado: detiene hilos, vacía la cola y cierra el CSV."""

        if self._closing:
            return

        self._closing = True

        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass

        try:
            clean = self.backend.controller.shutdown(
                timeout=SHUTDOWN_TIMEOUT_SECONDS
            )
        except Exception as error:
            clean = False
            print(f"Error al cerrar: {error}", file=sys.stderr)

        if not clean:
            print(
                "Aviso: algunos hilos no terminaron a tiempo "
                "(son daemon y no bloquean el cierre).",
                file=sys.stderr,
            )

        # Eventos finales (vehicle_finished, controller_shutdown...).
        self.dispatcher.drain_all()
        self.backend.logger.close()

        self.root.destroy()


def main() -> None:
    # Se importan aquí para que main.py pueda probarse sin Tkinter.
    import tkinter as tk

    from ui.main_window import MainWindow

    root = tk.Tk()
    app = Application(root, MainWindow)
    app.start()
    root.mainloop()


if __name__ == "__main__":
    main()