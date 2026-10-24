import csv
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from threading import Lock
from time import time
from typing import IO

from config import EVENT_LOG_PATH


class EventLogger:
    """
    Registra los eventos de la simulación en un archivo CSV.

    Pensado para ser alimentado por UN único consumidor de la cola de
    eventos (el bucle after() de la interfaz), que reenvía cada evento
    al logger, a las métricas y a la vista. Aun así es seguro usarlo
    desde varios hilos: las escrituras están protegidas por un Lock.

    Garantías:
    - Registrar eventos nunca interrumpe la simulación: si el archivo
      no se puede abrir o escribir, el logger se desactiva, guarda el
      error en last_error y devuelve False, pero no lanza excepciones.
    - Una fila por evento; el archivo se vacía (flush) en cada llamada
      a log() o log_many(), así los datos sobreviven a un cierre brusco.
    - La columna "session" aumenta cada vez que se reinicia la
      simulación, y "elapsed_s" cuenta desde el primer evento de la
      sesión: permite comparar experimentos dentro de un mismo archivo.
    """

    COLUMNS: tuple[str, ...] = (
        "session",
        "timestamp",
        "datetime",
        "elapsed_s",
        "event_type",
        "vehicle_id",
        "status",
        "parking_space",
        "arrival_delay",
        "parking_duration",
        "waiting_time",
        "parking_time",
        "reason",
        "extra",
    )

    # Campos del evento que van a una columna propia. Todo lo demás
    # (error, auto_generation, clean, ...) se guarda en "extra" como JSON.
    _MAPPED_KEYS = frozenset(
        {
            "type",
            "timestamp",
            "vehicle_id",
            "status",
            "parking_space",
            "released_space",
            "arrival_delay",
            "parking_duration",
            "waiting_time",
            "parking_time",
            "reason",
        }
    )

    # Un CSV abierto con una hoja de cálculo puede interpretar como
    # fórmula un texto que empiece con estos caracteres.
    _FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        append: bool = False,
        enabled: bool = True,
    ) -> None:
        """
        path: archivo CSV. Por defecto EVENT_LOG_PATH de config.py.
        append: True conserva el contenido previo (sin repetir la
                cabecera); False (por defecto) crea el archivo de nuevo.
        enabled: False crea un logger inactivo que ignora los eventos.
        """

        self._path = Path(path) if path is not None else Path(EVENT_LOG_PATH)
        self._append = append
        self._enabled = enabled

        self._lock = Lock()
        self._file: IO[str] | None = None
        self._writer: csv.DictWriter | None = None

        # _closed: tras close() no se registra nada hasta llamar open().
        # _opened_before: si se reabre, se añade al archivo sin borrarlo.
        self._closed = False
        self._opened_before = False

        self._rows_written = 0
        self._session = 1
        self._session_start: float | None = None
        self._last_error: str | None = None

    # --------------------------------------------------
    # PROPIEDADES
    # --------------------------------------------------

    @property
    def path(self) -> Path:
        """Ruta del archivo CSV."""
        return self._path

    @property
    def enabled(self) -> bool:
        """False si se desactivó o si falló alguna operación de archivo."""
        with self._lock:
            return self._enabled

    @property
    def is_open(self) -> bool:
        """Indica si el archivo está abierto."""
        with self._lock:
            return self._file is not None

    @property
    def rows_written(self) -> int:
        """Número de eventos escritos en esta instancia."""
        with self._lock:
            return self._rows_written

    @property
    def session(self) -> int:
        """Número de la sesión actual (aumenta al reiniciar)."""
        with self._lock:
            return self._session

    @property
    def last_error(self) -> str | None:
        """Último error de archivo, o None si no ha habido ninguno."""
        with self._lock:
            return self._last_error

    # --------------------------------------------------
    # APERTURA Y CIERRE
    # --------------------------------------------------

    def open(self) -> bool:
        """
        Abre (o crea) el archivo y escribe la cabecera si hace falta.

        No es obligatorio llamarlo: log() abre el archivo la primera
        vez que se usa. Devuelve False si no se pudo abrir.
        """

        with self._lock:
            self._closed = False
            return self._open_unlocked()

    def _open_unlocked(self) -> bool:
        if not self._enabled or self._closed:
            return False

        if self._file is not None:
            return True

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)

            # Con append no se repite la cabecera si ya hay contenido.
            # Una reapertura nunca borra lo ya escrito.
            append = self._append or self._opened_before

            needs_header = (
                not append
                or not self._path.exists()
                or self._path.stat().st_size == 0
            )

            self._file = open(
                self._path,
                mode="a" if append else "w",
                encoding="utf-8",
                newline="",
            )

            self._writer = csv.DictWriter(
                self._file,
                fieldnames=self.COLUMNS,
                lineterminator="\n",
            )

            if needs_header:
                self._writer.writeheader()
                self._file.flush()

            self._opened_before = True
            return True

        except (OSError, csv.Error) as error:
            self._fail_unlocked(error)
            return False

    def flush(self) -> None:
        """Fuerza la escritura de los datos pendientes en disco."""

        with self._lock:
            self._flush_unlocked()

    def close(self) -> None:
        """Cierra el archivo. Es seguro llamarlo varias veces."""

        with self._lock:
            self._closed = True

            file = self._file
            self._file = None
            self._writer = None

            if file is not None:
                try:
                    file.close()
                except OSError as error:
                    self._last_error = f"{type(error).__name__}: {error}"

    def __enter__(self) -> "EventLogger":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    # --------------------------------------------------
    # REGISTRO DE EVENTOS
    # --------------------------------------------------

    def log(self, event: Mapping[str, object]) -> bool:
        """
        Registra un evento. Devuelve True si se escribió.
        Nunca lanza excepciones por problemas de archivo.
        """

        with self._lock:
            if not self._write_unlocked(event):
                return False

            self._flush_unlocked()
            return True

    def log_many(self, events: Iterable[Mapping[str, object]]) -> int:
        """
        Registra varios eventos con un solo flush.
        Devuelve cuántos se escribieron.
        """

        written = 0

        with self._lock:
            for event in events:
                if self._write_unlocked(event):
                    written += 1

            if written:
                self._flush_unlocked()

        return written

    # --------------------------------------------------
    # INTERNOS (se ejecutan con self._lock adquirido)
    # --------------------------------------------------

    def _write_unlocked(self, event: Mapping[str, object]) -> bool:
        if not self._enabled:
            return False

        if not self._open_unlocked():
            return False

        try:
            row = self._build_row(event)
            assert self._writer is not None
            self._writer.writerow(row)

        except (OSError, csv.Error) as error:
            self._fail_unlocked(error)
            return False

        self._rows_written += 1

        # Al reiniciar, la siguiente fila pertenece a una sesión nueva.
        if event.get("type") == "simulation_reset":
            self._session += 1
            self._session_start = None

        return True

    def _build_row(self, event: Mapping[str, object]) -> dict[str, object]:
        raw_timestamp = event.get("timestamp")

        try:
            timestamp = float(raw_timestamp)  # type: ignore[arg-type]
            # Comprueba que se pueda convertir a fecha: descarta NaN,
            # infinito y valores fuera del rango de la plataforma.
            datetime.fromtimestamp(timestamp)
        except (TypeError, ValueError, OverflowError, OSError):
            timestamp = time()

        if self._session_start is None:
            self._session_start = timestamp

        # Las salidas informan la plaza liberada como released_space.
        parking_space = event.get("parking_space")
        if parking_space is None:
            parking_space = event.get("released_space")

        extra = {
            key: value
            for key, value in event.items()
            if key not in self._MAPPED_KEYS
        }

        return {
            "session": self._session,
            "timestamp": f"{timestamp:.6f}",
            "datetime": datetime.fromtimestamp(timestamp).isoformat(
                timespec="milliseconds"
            ),
            "elapsed_s": f"{timestamp - self._session_start:.3f}",
            "event_type": self._clean(event.get("type", "unknown_event")),
            "vehicle_id": self._clean(event.get("vehicle_id")),
            "status": self._clean(event.get("status")),
            "parking_space": self._clean(parking_space),
            "arrival_delay": self._clean(event.get("arrival_delay")),
            "parking_duration": self._clean(event.get("parking_duration")),
            "waiting_time": self._clean(event.get("waiting_time")),
            "parking_time": self._clean(event.get("parking_time")),
            "reason": self._clean(event.get("reason")),
            "extra": (
                self._clean(
                    json.dumps(
                        extra,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                )
                if extra
                else ""
            ),
        }

    def _clean(self, value: object) -> object:
        """Convierte None en vacío y neutraliza posibles fórmulas."""

        if value is None:
            return ""

        if isinstance(value, str) and value.startswith(
            self._FORMULA_PREFIXES
        ):
            return "'" + value

        return value

    def _flush_unlocked(self) -> None:
        if self._file is None:
            return

        try:
            self._file.flush()
        except OSError as error:
            self._fail_unlocked(error)

    def _fail_unlocked(self, error: Exception) -> None:
        """Desactiva el logger sin interrumpir la simulación."""

        self._last_error = f"{type(error).__name__}: {error}"
        self._enabled = False

        file = self._file
        self._file = None
        self._writer = None

        if file is not None:
            try:
                file.close()
            except OSError:
                pass