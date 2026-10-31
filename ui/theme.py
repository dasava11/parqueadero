"""Paleta, estilos por estado y utilidades comunes de la interfaz.

Aqui vive todo lo que comparten main_window, control_panel, parking_view y
metrics_board, para no repetir colores ni logica de formato.

Las utilidades son tolerantes: aceptan objetos o diccionarios (get_field) y
Enum o texto (status_key), de modo que la UI no se rompe si el backend cambia
un detalle menor de representacion.
"""
from __future__ import annotations

FONT = "Helvetica"  # Tk la traduce a una sans-serif disponible en cada sistema

# --- Paleta ---------------------------------------------------------------
BG = "#eef2f7"       # fondo de la ventana
CARD = "#ffffff"     # fondo de tarjetas
HEADER = "#1e293b"   # barra superior
INK = "#0f172a"      # texto principal
MUTED = "#64748b"    # texto secundario
BORDER = "#d5dde8"

GREEN = "#16a34a"
YELLOW = "#eab308"
RED = "#dc2626"
BLUE = "#2563eb"

# --- Numeracion de plazas ---------------------------------------------------
# core/parking.py numera las plazas de 1 a capacidad (occupy_space devuelve
# indice + 1). Con None la UI lo detectaria sola, pero aqui es un dato conocido.
SPACE_NUMBER_BASE: int | None = 1

# --- Estilos por estado de vehiculo ----------------------------------------
# clave (nombre del Enum en minusculas) -> (etiqueta, fondo, texto, color fuerte)
STATUS_STYLE = {
    "generated":   ("Generado",    "#f1f5f9", "#475569", "#94a3b8"),
    "approaching": ("Llegando",    "#e0f2fe", "#075985", "#38bdf8"),
    "waiting":     ("En cola",     "#fef3c7", "#92400e", "#f59e0b"),
    "parked":      ("Estacionado", "#dbeafe", "#1e40af", "#2563eb"),
    "exiting":     ("Saliendo",    "#ffedd5", "#9a3412", "#ea580c"),
    "finished":    ("Finalizado",  "#f3f4f6", "#6b7280", "#9ca3af"),
    "cancelled":   ("Cancelado",   "#fee2e2", "#991b1b", "#ef4444"),
}
DEFAULT_STYLE = ("Desconocido", "#f1f5f9", "#475569", "#94a3b8")

EXIT_REASON_LABELS = {
    "parking_duration_elapsed": "por duración",
    "manual_exit": "manual",
    "simulation_stopped": "por parada",
}


# --- Semaforo del parqueadero -------------------------------------------------
# Verde: hay plazas. Amarillo: quedan pocas (aviso previo a la saturacion).
# Rojo: lleno (los vehiculos que lleguen esperan en la cola).
# Es solo una señal visual: no existe un estado equivalente en el backend.
LOW_SPACES_FRACTION = 0.2  # amarillo cuando quedan <= 20 % de las plazas libres


def low_spaces_threshold(capacity: int) -> int:
    """Plazas libres a partir de las cuales el semaforo pasa a amarillo."""
    return max(1, round(capacity * LOW_SPACES_FRACTION))


def traffic_light_level(free: int, capacity: int) -> int:
    """0 = verde, 1 = amarillo, 2 = rojo."""
    if free <= 0:
        return 2
    if free <= low_spaces_threshold(capacity):
        return 1
    return 0


SCROLLBAR_STYLE = "Modern.Vertical.TScrollbar"


def configure_scrollbar_style(style) -> None:
    """Barra delgada y plana, sin flechas (reemplaza la de 'clam')."""
    style.layout(SCROLLBAR_STYLE, [
        ("Vertical.Scrollbar.trough", {
            "sticky": "ns",
            "children": [("Vertical.Scrollbar.thumb",
                          {"expand": "1", "sticky": "nswe"})],
        })])
    style.configure(SCROLLBAR_STYLE, width=8, gripcount=0, borderwidth=0,
                    relief="flat", troughcolor="#e8edf3",
                    background="#b6c2d1", bordercolor="#e8edf3",
                    lightcolor="#b6c2d1", darkcolor="#b6c2d1")
    style.map(SCROLLBAR_STYLE,
              background=[("pressed", "#64748b"), ("active", "#94a3b8")],
              lightcolor=[("pressed", "#64748b"), ("active", "#94a3b8")],
              darkcolor=[("pressed", "#64748b"), ("active", "#94a3b8")])


def status_key(status) -> str:
    """'VehicleStatus.PARKED', VehicleStatus.PARKED o 'parked' -> 'parked'."""
    name = getattr(status, "name", None)
    text = name if isinstance(name, str) else str(status)
    return text.split(".")[-1].strip().lower()


def style_of(status):
    return STATUS_STYLE.get(status_key(status), DEFAULT_STYLE)


def get_field(obj, name, default=None):
    """Lee un campo de un diccionario o de un objeto."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def read_flag(obj, name) -> bool:
    """Lee una propiedad booleana o un metodo sin argumentos."""
    value = getattr(obj, name)
    return bool(value() if callable(value) else value)


def vehicle_label(vehicle_id) -> str:
    if isinstance(vehicle_id, int):
        return f"V{vehicle_id:02d}"
    return str(vehicle_id)


def to_float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number  # descarta NaN


def fmt_seconds(value, empty: str = "-") -> str:
    number = to_float(value)
    return f"{number:.1f} s" if number > 0 else empty


def fmt_clock(seconds) -> str:
    minutes, secs = divmod(int(max(0.0, to_float(seconds))), 60)
    return f"{minutes:02d}:{secs:02d}"


def to_percent(value) -> float:
    """Acepta fraccion (0-1) o porcentaje (0-100) y devuelve 0-100."""
    number = to_float(value)
    if number <= 1.0:
        number *= 100.0
    return max(0.0, min(100.0, number))