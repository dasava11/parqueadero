from dataclasses import dataclass
from enum import Enum


class VehicleStatus(str, Enum):
    GENERATED = "Generado"
    APPROACHING = "Llegando"
    WAITING = "En espera"
    PARKED = "Estacionado"
    EXITING = "Saliendo"
    FINISHED = "Finalizado"
    CANCELED = "Cancelado"


@dataclass
class Vehicle:
    vehicle_id: str
    arrival_delay: float
    parking_duration: float

    status: VehicleStatus = VehicleStatus.GENERATED
    waiting_time: float = 0.0
    parking_time: float = 0.0
    parking_space: int | None = None