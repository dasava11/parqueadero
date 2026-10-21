
from pathlib import Path

# Parámetros del parqueadero
PARKING_CAPACITY = 20


# Generación automática de vehículos
AUTO_GENERATION_ENABLED = True

AUTO_ARRIVAL_INTERVAL_MIN_SECONDS = 3.0
AUTO_ARRIVAL_INTERVAL_MAX_SECONDS = 6.0


# Tiempos individuales de los vehículos
VEHICLE_ARRIVAL_DELAY_MIN_SECONDS = 5.0
VEHICLE_ARRIVAL_DELAY_MAX_SECONDS = 15.0

PARKING_DURATION_MIN_SECONDS = 5.0
PARKING_DURATION_MAX_SECONDS = 30.0


# Concurrencia
PARKING_ACQUIRE_POLL_INTERVAL_SECONDS = 0.25


# Interfaz gráfica
UI_REFRESH_INTERVAL_MS = 100


# Registro de eventos
PROJECT_ROOT = Path(__file__).resolve().parent
EVENT_LOG_PATH = PROJECT_ROOT / "events.csv"


# Validación de configuración
if (
    isinstance(PARKING_CAPACITY, bool)
    or not isinstance(PARKING_CAPACITY, int)
    or PARKING_CAPACITY <= 0
):
    raise ValueError(
        "PARKING_CAPACITY debe ser un entero mayor que cero."
    )

if not (
    0 < AUTO_ARRIVAL_INTERVAL_MIN_SECONDS
    <= AUTO_ARRIVAL_INTERVAL_MAX_SECONDS
):
    raise ValueError("El intervalo de generación no es válido.")

if not (
    0 <= VEHICLE_ARRIVAL_DELAY_MIN_SECONDS
    <= VEHICLE_ARRIVAL_DELAY_MAX_SECONDS
):
    raise ValueError("El retraso de llegada no es válido.")

if not (
    0 < PARKING_DURATION_MIN_SECONDS
    <= PARKING_DURATION_MAX_SECONDS
):
    raise ValueError("La duración del estacionamiento no es válida.")

if PARKING_ACQUIRE_POLL_INTERVAL_SECONDS <= 0:
    raise ValueError(
        "El intervalo de adquisición debe ser mayor que cero."
    )

if (
    isinstance(UI_REFRESH_INTERVAL_MS, bool)
    or not isinstance(UI_REFRESH_INTERVAL_MS, int)
    or UI_REFRESH_INTERVAL_MS <= 0
):
    raise ValueError(
        "UI_REFRESH_INTERVAL_MS debe ser un entero positivo."
    )
