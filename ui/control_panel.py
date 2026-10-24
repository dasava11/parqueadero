"""Tablero de control: botones de la simulacion y su estado.

Reemplaza a nav_bar.py. No conoce al controller: recibe callbacks y expone
set_state() para habilitar o deshabilitar cada boton segun el estado real.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.theme import BORDER, CARD, FONT, GREEN, MUTED, YELLOW

# clave, texto, color base, color al pasar el mouse
_BUTTONS = (
    ("start", "Iniciar simulación", "#16a34a", "#15803d"),
    ("add", "Agregar vehículo", "#2563eb", "#1d4ed8"),
    ("stop", "Detener", "#dc2626", "#b91c1c"),
    ("reset", "Reiniciar", "#64748b", "#475569"),
    ("exit", "Salida manual", "#ea580c", "#c2410c"),
)


class ControlPanel(tk.Frame):
    """Fila de botones y un indicador de estado (detenida / en ejecución)."""

    def __init__(self, master, on_start, on_add, on_stop, on_reset, on_exit):
        super().__init__(master, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1)
        callbacks = {"start": on_start, "add": on_add, "stop": on_stop,
                     "reset": on_reset, "exit": on_exit}
        self._configure_styles()

        self._buttons: dict[str, ttk.Button] = {}
        for key, text, _base, _hover in _BUTTONS:
            button = ttk.Button(self, text=text, style=f"Cp{key}.TButton",
                                command=callbacks[key], takefocus=True)
            button.pack(side="left", padx=(12 if key == "start" else 6, 0),
                        pady=10)
            self._buttons[key] = button

        self._status = tk.Label(self, text="● Detenida", bg=CARD, fg=MUTED,
                                font=(FONT, 11, "bold"))
        self._status.pack(side="right", padx=16)

    @staticmethod
    def _configure_styles() -> None:
        style = ttk.Style()
        for key, _text, base, hover in _BUTTONS:
            name = f"Cp{key}.TButton"
            style.configure(name, background=base, foreground="white",
                            font=(FONT, 10, "bold"), padding=(14, 8),
                            borderwidth=0, focusthickness=2,
                            focuscolor=hover)
            style.map(name,
                      background=[("disabled", "#cbd5e1"), ("active", hover)],
                      foreground=[("disabled", "#94a3b8")])

    def set_state(self, running: bool, idle: bool, can_exit: bool) -> None:
        """Habilita los botones segun el estado real del controller.

        running  -> controller.is_running
        idle     -> controller.is_idle (no queda ningun hilo activo)
        can_exit -> hay un vehiculo ESTACIONADO seleccionado
        """
        self._enable("start", idle and not running)
        self._enable("add", running)
        self._enable("stop", running)
        self._enable("reset", True)
        self._enable("exit", can_exit)

        if running:
            self._status.configure(text="● En ejecución", fg=GREEN)
        elif not idle:
            self._status.configure(text="● Deteniendo…", fg=YELLOW)
        else:
            self._status.configure(text="● Detenida", fg=MUTED)

    def _enable(self, key: str, enabled: bool) -> None:
        button = self._buttons[key]
        wanted = "normal" if enabled else "disabled"
        if str(button.cget("state")) != wanted:
            button.configure(state=wanted)