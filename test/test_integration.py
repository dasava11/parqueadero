"""Prueba de integración SIN GUI: controller + workers + ParkingLot + dispatcher + metrics + logger.
Ejecutar desde la raíz del proyecto:  python3 tests/test_integration.py
Los parámetros se reducen ANTES de importar el resto para que la prueba sea rápida.
"""
import csv
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.PARKING_CAPACITY = 3
config.AUTO_ARRIVAL_INTERVAL_MIN_SECONDS = 0.2
config.AUTO_ARRIVAL_INTERVAL_MAX_SECONDS = 0.5
config.VEHICLE_ARRIVAL_DELAY_MIN_SECONDS = 0.0
config.VEHICLE_ARRIVAL_DELAY_MAX_SECONDS = 0.3
config.MANUAL_VEHICLE_ARRIVAL_DELAY_MIN_SECONDS = 0.0
config.MANUAL_VEHICLE_ARRIVAL_DELAY_MAX_SECONDS = 0.1
config.PARKING_DURATION_MIN_SECONDS = 0.3
config.PARKING_DURATION_MAX_SECONDS = 1.2
config.PARKING_ACQUIRE_POLL_INTERVAL_SECONDS = 0.02

from core.controller import SimulationController
from services.dispatcher import EventDispatcher
from services.event_logger import EventLogger
from services.metrics import Metrics

CAP = config.PARKING_CAPACITY
thread_errors: list[str] = []
threading.excepthook = lambda a: thread_errors.append(f"{a.thread.name}: {a.exc_type.__name__}: {a.exc_value}")

violations: list[str] = []
monitor_stop = threading.Event()
samples = 0


def monitor(lot):
    global samples
    while not monitor_stop.is_set():
        s = lot.get_snapshot()
        samples += 1
        ids = [v.vehicle_id for v in s["parked_vehicles"]]
        if s["occupied_count"] > CAP:
            violations.append(f"ocupadas {s['occupied_count']} > {CAP}")
        if len(ids) != len(set(ids)):
            violations.append(f"vehículo duplicado en plazas: {ids}")
        if s["occupied_count"] + s["available_count"] != CAP:
            violations.append("ocupadas + libres != capacidad")
        wids = [v.vehicle_id for v in s["waiting_vehicles"]]
        if len(wids) != len(set(wids)) or set(wids) & set(ids):
            violations.append("cola inconsistente")
        monitor_stop.wait(0.005)


def pump(dispatcher, seconds, actions=None):
    """Imita el after() de Tkinter: drena la cola cada 20 ms."""
    t0 = time.monotonic()
    pending = sorted((actions or {}).items())
    while time.monotonic() - t0 < seconds:
        el = time.monotonic() - t0
        while pending and pending[0][0] <= el:
            pending.pop(0)[1]()
        dispatcher.drain()
        time.sleep(0.02)


def wait_idle(c, d, limit=5.0):
    t0 = time.monotonic()
    while not c.is_idle and time.monotonic() - t0 < limit:
        d.drain()
        time.sleep(0.01)
    return time.monotonic() - t0


def assert_clean(c, label):
    s = c.parking_lot.get_snapshot()
    sem = c.parking_lot._space_semaphore._value
    assert s["occupied_count"] == 0 and s["waiting_count"] == 0, (label, s)
    assert sem == CAP, (label, "permisos", sem)


def main():
    tmp = tempfile.TemporaryDirectory()
    csv_path = Path(tmp.name) / "logs" / "events.csv"
    logger, metrics, events = EventLogger(csv_path), Metrics(CAP), []
    controller = SimulationController()
    disp = EventDispatcher(controller.event_queue, [logger.log, metrics.consume, events.append])
    threading.Thread(target=monitor, args=(controller.parking_lot,), daemon=True).start()

    # ---- Fase A: carrera normal + salida manual + rechazos ----
    controller.start_simulation()
    assert controller.start_simulation() is False
    manual = {"exit_ok": None, "rejects": 0}

    def manual_vehicles():
        for _ in range(3):
            controller.add_manual_vehicle()

    def manual_exit():
        for _ in range(50):
            for v in controller.parking_lot.parked_vehicles:
                try:
                    controller.request_vehicle_exit(v.vehicle_id)
                    manual["exit_ok"] = v.vehicle_id
                    return
                except ValueError:
                    continue
            time.sleep(0.02)

    def rejects():
        try:
            controller.request_vehicle_exit("V999")
        except ValueError:
            manual["rejects"] += 1
        w = controller.parking_lot.waiting_vehicles
        if w:
            try:
                controller.request_vehicle_exit(w[0].vehicle_id)
            except ValueError:
                manual["rejects"] += 1

    pump(disp, 12.0, {2.0: manual_vehicles, 5.0: manual_exit, 7.0: rejects})
    mid = metrics.snapshot()

    # ---- Detener ----
    controller.stop_simulation()
    t_idle = wait_idle(controller, disp)
    disp.drain_all()
    assert controller.is_idle and not controller.is_running
    assert_clean(controller, "tras detener")
    try:
        controller.add_vehicle()
        raise AssertionError("add_vehicle debía fallar con la simulación detenida")
    except RuntimeError:
        pass
    last_id_a = max(v.vehicle_id for v in controller.vehicles)

    # ---- Fase B: reanudar sin reiniciar (los ids continúan) ----
    controller.start_simulation()
    pump(disp, 3.0)
    assert max(v.vehicle_id for v in controller.vehicles) > last_id_a
    controller.stop_simulation()
    wait_idle(controller, disp)
    disp.drain_all()
    assert_clean(controller, "tras reanudar y detener")

    # ---- Fase C: 3 ciclos iniciar -> reiniciar (con vehículos estacionados y en cola) ----
    reset_times = []
    for _ in range(3):
        controller.start_simulation()
        pump(disp, 3.0)
        n_parked = controller.parking_lot.occupied_count
        t0 = time.monotonic()
        controller.reset_simulation(timeout=5.0)
        reset_times.append((time.monotonic() - t0, n_parked))
        disp.drain_all()
        assert_clean(controller, "tras reiniciar")
        assert controller.vehicles == () and controller.is_idle

    # ---- Cierre ----
    controller.start_simulation()
    pump(disp, 2.0)
    clean = controller.shutdown(timeout=5.0)
    disp.drain_all()
    monitor_stop.set()
    logger.close()
    assert clean is True
    assert_clean(controller, "tras shutdown")
    alive = [t.name for t in threading.enumerate() if t.name.startswith("vehicle")]

    # ---- Verificación de secuencias por vehículo, por sesión ----
    segments, cur = [], []
    for e in events:
        if e["type"] == "simulation_reset":
            segments.append(cur)
            cur = []
        else:
            cur.append(e)
    segments.append(cur)

    OK = {
        ("vehicle_created", "vehicle_approaching", "vehicle_cancelled"),
        ("vehicle_created", "vehicle_approaching", "vehicle_waiting", "vehicle_cancelled"),
        ("vehicle_created", "vehicle_approaching", "vehicle_waiting", "vehicle_parked",
         "vehicle_exiting", "vehicle_finished"),
    }
    bad, totals = [], {"created": 0, "finished": 0, "cancelled": 0}
    reasons = {}
    for seg in segments:
        per: dict[str, list[str]] = {}
        for e in seg:
            vid = e.get("vehicle_id")
            if vid and e["type"] != "vehicle_exit_requested":
                per.setdefault(vid, []).append(e["type"])
            if e["type"] == "vehicle_finished":
                reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
        for vid, seq in per.items():
            totals["created"] += 1
            totals["finished"] += seq[-1] == "vehicle_finished"
            totals["cancelled"] += seq[-1] == "vehicle_cancelled"
            if tuple(seq) not in OK:
                bad.append((vid, seq))

    firsts = [next(e for e in seg if e["type"] == "vehicle_created") for seg in segments if seg]
    seeds_equal = all(
        (f["arrival_delay"], f["parking_duration"]) == (firsts[0]["arrival_delay"], firsts[0]["parking_duration"])
        for f in firsts[:4]  # sesión A y los 3 ciclos tras reiniciar
    )

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    sessions = sorted({int(r["session"]) for r in rows})

    print(f"muestras del monitor de invariantes : {samples}")
    print(f"violaciones de invariantes          : {len(violations)} {violations[:3]}")
    print(f"errores en hilos                    : {len(thread_errors)} {thread_errors[:3]}")
    print(f"hilos de vehículo vivos al cerrar   : {alive}")
    print(f"errores de manejadores (dispatcher) : {disp.error_count} {disp.errors[:2]}")
    print(f"fase A: max ocupadas={mid['max_occupied']}, cola máx={mid['max_queue']}, "
          f"espera prom={mid['avg_wait']:.2f}s, utilización={mid['utilization']:.0%}")
    print(f"salida manual aceptada: {manual['exit_ok']} | rechazos correctos: {manual['rejects']}")
    print(f"detener -> idle: {t_idle*1000:.0f} ms")
    print("reinicios (ms, estacionados al reiniciar): " +
          ", ".join(f"{t*1000:.0f} ({n})" for t, n in reset_times))
    print(f"vehículos: {totals}  motivos de salida: {reasons}")
    print(f"secuencias de eventos inválidas     : {len(bad)} {bad[:2]}")
    print(f"misma semilla -> mismo 1er vehículo en cada sesión: {seeds_equal}")
    print(f"CSV: {len(rows)} filas vs {len(events)} eventos; sesiones {sessions}; "
          f"logger.last_error={logger.last_error}")

    assert not violations and not thread_errors and not alive and disp.error_count == 0
    assert mid["max_occupied"] == CAP and mid["max_queue"] > 0
    assert manual["exit_ok"] and manual["rejects"] >= 1
    assert "manual_exit" in reasons and "simulation_stopped" in reasons
    assert not bad and totals["created"] == totals["finished"] + totals["cancelled"]
    assert seeds_equal and len(rows) == len(events) and sessions == [1, 2, 3, 4]
    assert t_idle < 2.0 and all(t < 1.0 for t, _ in reset_times)
    print("\nINTEGRACIÓN OK")
    tmp.cleanup()


if __name__ == "__main__":
    main()