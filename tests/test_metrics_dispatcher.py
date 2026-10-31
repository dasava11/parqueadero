"""Pruebas sin GUI de Metrics, EventDispatcher y EventLogger. Ejecutar desde la raíz:
    python3 tests/test_metrics_dispatcher.py
"""
import csv
import sys
import tempfile
from pathlib import Path
from queue import Queue

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.dispatcher import EventDispatcher
from services.event_logger import EventLogger
from services.metrics import Metrics


def ev(t, type_, vid=None, **kw):
    e = {"type": type_, "timestamp": 1000.0 + t, **kw}
    if vid:
        e["vehicle_id"] = vid
    return e


def scenario():
    return [
        ev(0, "simulation_started", auto_generation=True),
        *[ev(0, "vehicle_created", v) for v in ("V1", "V2", "V3", "V4")],
        ev(1, "vehicle_waiting", "V1"),
        ev(1, "vehicle_parked", "V1", parking_space=1, waiting_time=0.0),
        ev(2, "vehicle_waiting", "V2"),
        ev(2, "vehicle_parked", "V2", parking_space=2, waiting_time=0.0),
        ev(3, "vehicle_waiting", "V3"),
        ev(3.5, "vehicle_waiting", "V4"),
        ev(5, "vehicle_finished", "V1", released_space=1, parking_time=4.0, reason="parking_duration_elapsed"),
        ev(5, "vehicle_parked", "V3", parking_space=1, waiting_time=2.0),
        ev(6, "vehicle_cancelled", "V4", reason="entry_cancelled"),
        ev(8, "vehicle_finished", "V2", released_space=2, parking_time=6.0, reason="manual_exit"),
        ev(10, "vehicle_finished", "V3", released_space=1, parking_time=5.0, reason="simulation_stopped"),
        ev(10, "controller_shutdown", clean=True),  # sin vehicle_id
    ]


def test_metrics():
    m = Metrics(capacity=2)
    for e in scenario():
        m.consume(e)
    s = m.snapshot()
    assert s["created"] == 4 and s["served"] == 3 and s["cancelled"] == 1, s
    assert s["max_occupied"] == 2 and s["max_queue"] == 2, s
    assert s["occupied_now"] == 0 and s["waiting_now"] == 0, s
    assert abs(s["avg_wait"] - 2 / 3) < 1e-9 and s["max_wait"] == 2.0, s
    assert abs(s["avg_parking"] - 5.0) < 1e-9, s
    assert abs(s["utilization"] - 0.75) < 1e-9, s      # 15 plaza-s / (2 * 10 s)
    assert s["exit_reasons"] == {
        "parking_duration_elapsed": 1, "manual_exit": 1, "simulation_stopped": 1}, s
    m.consume(ev(11, "simulation_reset"))
    z = m.snapshot()
    assert z["created"] == 0 and z["duration"] == 0.0 and z["utilization"] == 0.0, z


def test_metrics_out_of_order_and_bad_data():
    m = Metrics(2)
    m.consume(ev(5, "vehicle_parked", "A", waiting_time=1.0))
    m.consume(ev(3, "vehicle_finished", "A", parking_time="x", reason=None))  # timestamp anterior
    s = m.snapshot()
    assert s["utilization"] == 0.0 and s["served"] == 1 and s["avg_parking"] == 0.0, s
    m.consume({"type": "vehicle_parked"})  # sin id ni timestamp: no debe fallar


def test_dispatcher_and_logger():
    q: Queue = Queue()
    m = Metrics(2)
    seen = []

    def broken(event):
        raise RuntimeError("falla simulada")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "logs" / "events.csv"
        logger = EventLogger(path)
        d = EventDispatcher(q, [broken, logger.log, m.consume, seen.append], max_events_per_tick=5)

        events = scenario()
        for e in events:
            q.put(e)

        first = d.drain()
        assert first == 5 and q.qsize() == len(events) - 5      # respeta el tope por ciclo
        d.drain_all()
        logger.close()

        assert len(seen) == len(events), "cada evento se entrega UNA vez a cada manejador"
        assert d.error_count == len(events) and "falla simulada" in d.errors[-1]
        assert m.snapshot()["served"] == 3                       # el manejador roto no afectó al resto

        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(events), len(rows)
        assert rows[0]["event_type"] == "simulation_started" and rows[0]["elapsed_s"] == "0.000"
        assert rows[-1]["vehicle_id"] == "" and rows[-1]["elapsed_s"] == "10.000"
        parked = [r for r in rows if r["event_type"] == "vehicle_parked"]
        fin = [r for r in rows if r["event_type"] == "vehicle_finished"]
        assert [r["parking_space"] for r in parked] == ["1", "2", "1"]
        assert [r["parking_space"] for r in fin] == ["1", "2", "1"]   # released_space -> parking_space


def test_logger_robustness():
    with tempfile.TemporaryDirectory() as tmp:
        lg = EventLogger(Path(tmp) / "e.csv")
        assert lg.log({"type": "vehicle_error", "vehicle_id": "=cmd()", "status": "-x"}) is True
        lg.log({"type": "simulation_reset", "timestamp": 1.0})
        lg.log({"type": "x", "timestamp": 2.0})
        lg.close()
        rows = list(csv.DictReader(open(Path(tmp) / "e.csv", encoding="utf-8")))
        assert rows[0]["vehicle_id"] == "'=cmd()" and rows[0]["status"] == "'-x"
        assert [r["session"] for r in rows] == ["1", "1", "2"]
        # E1: timestamps inválidos no deben lanzar ni desactivar el logger.
        lg2 = EventLogger(Path(tmp) / "e2.csv")
        bad_values = (float("nan"), float("inf"), 1e30, -1e30, "abc", None)
        for bad in bad_values:
            assert lg2.log({"type": "x", "timestamp": bad}) is True, bad
        assert lg2.enabled and lg2.last_error is None
        lg2.close()
        rows2 = list(csv.DictReader(open(Path(tmp) / "e2.csv", encoding="utf-8")))
        assert len(rows2) == len(bad_values)
        assert all(r["datetime"] for r in rows2) and all(r["elapsed_s"] for r in rows2)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)