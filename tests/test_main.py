"""Prueba de Application (main.py) sin Tkinter: usa un root y una ventana falsos.
    python3 tests/test_main.py
"""
import csv
import heapq
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.PARKING_CAPACITY = 3
config.AUTO_ARRIVAL_INTERVAL_MIN_SECONDS = 0.1
config.AUTO_ARRIVAL_INTERVAL_MAX_SECONDS = 0.3
config.VEHICLE_ARRIVAL_DELAY_MIN_SECONDS = 0.0
config.VEHICLE_ARRIVAL_DELAY_MAX_SECONDS = 0.2
config.PARKING_DURATION_MIN_SECONDS = 0.3
config.PARKING_DURATION_MAX_SECONDS = 1.0
config.PARKING_ACQUIRE_POLL_INTERVAL_SECONDS = 0.02
config.UI_REFRESH_INTERVAL_MS = 20

from main import Application, build_backend


class FakeRoot:
    """Imita after / after_cancel / protocol / destroy de Tk."""

    def __init__(self):
        self._queue, self._cancelled, self._n = [], set(), 0
        self.handlers, self.destroyed = {}, False

    def after(self, ms, fn):
        self._n += 1
        heapq.heappush(self._queue, (time.monotonic() + ms / 1000, self._n, fn))
        return self._n

    def after_cancel(self, ident):
        self._cancelled.add(ident)

    def protocol(self, name, fn):
        self.handlers[name] = fn

    def destroy(self):
        self.destroyed = True

    def run_for(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.destroyed:
            if self._queue and self._queue[0][0] <= time.monotonic():
                _, ident, fn = heapq.heappop(self._queue)
                if ident not in self._cancelled:
                    fn()
            else:
                time.sleep(0.002)


class FakeWindow:
    def __init__(self, root, controller, metrics, broken=False):
        self.controller, self.metrics, self.broken = controller, metrics, broken
        self.events, self.refreshes = [], 0

    def on_event(self, event):
        self.events.append(event)
        if self.broken:
            raise RuntimeError("vista rota")

    def refresh(self):
        self.refreshes += 1
        self.controller.parking_lot.get_snapshot()
        self.metrics.snapshot()
        if self.broken:
            raise RuntimeError("refresh roto")


def vehicle_threads():
    return [t.name for t in threading.enumerate() if t.name.startswith("vehicle")]


def run_case(broken):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "logs" / "events.csv"
        root = FakeRoot()
        app = Application(root, lambda r, c, m: FakeWindow(r, c, m, broken),
                          backend=build_backend(path))
        assert "WM_DELETE_WINDOW" in root.handlers
        app.start()

        c = app.backend.controller
        c.start_simulation()
        root.run_for(1.5)
        c.add_manual_vehicle()
        root.run_for(1.5)

        snap = app.backend.metrics.snapshot()
        assert snap["created"] > 5 and snap["max_occupied"] >= 1, snap

        root.handlers["WM_DELETE_WINDOW"]()      # el usuario cierra la ventana
        root.handlers["WM_DELETE_WINDOW"]()      # cerrar dos veces es seguro
        assert root.destroyed and c.is_idle and not vehicle_threads()
        assert app.backend.logger.is_open is False

        lot = c.parking_lot.get_snapshot()
        assert lot["occupied_count"] == 0 and lot["waiting_count"] == 0
        assert c.parking_lot._space_semaphore._value == config.PARKING_CAPACITY

        rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
        assert rows[-1]["event_type"] == "controller_shutdown"
        assert app.dispatcher.drain() == 0       # cola vacía tras el cierre
        window = app.window
        assert len(window.events) == len(rows), (len(window.events), len(rows))
        assert window.refreshes > 10
        return app, rows, snap


def test_normal():
    app, rows, snap = run_case(broken=False)
    assert app.dispatcher.error_count == 0
    print(f"  normal: {len(rows)} eventos, {app.window.refreshes} refrescos, creados={snap['created']}")


def test_vista_rota_no_afecta_al_resto():
    app, rows, snap = run_case(broken=True)
    assert app.dispatcher.error_count == len(rows)      # on_event falló en cada evento
    assert snap["created"] > 5 and len(rows) > 10       # logger y métricas siguieron
    print(f"  vista rota: {app.dispatcher.error_count} fallos aislados, logger y métricas intactos")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
