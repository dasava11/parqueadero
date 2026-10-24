"""Tablero de metricas: muestra Metrics.snapshot() sin calcular nada.

Toda la logica de calculo vive en services/metrics.py; aqui solo se formatea.
Las claves que se leen son las documentadas en el informe: created, served,
cancelled, errors, exit_reasons, waiting_now, occupied_now, max_occupied,
max_queue, avg_wait, max_wait, avg_parking, utilization y duration.
Si falta alguna, se muestra un valor por defecto en vez de fallar.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui.theme import (BLUE, BORDER, CARD, EXIT_REASON_LABELS, FONT, INK,
                      MUTED, fmt_clock, get_field, to_float, to_percent)

_TILES = (
    ("created", "Creados"), ("served", "Atendidos"),
    ("cancelled", "Cancelados"), ("errors", "Errores"),
    ("occupancy", "Ocupación (act. / máx.)"),
    ("queue", "Cola (act. / máx.)"), ("avg_wait", "Espera promedio"),
    ("max_wait", "Espera máxima"), ("avg_parking", "Estac. promedio"),
    ("duration", "Tiempo simulado"),
)
_PER_ROW = 5


class MetricsBoard(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1)
        tk.Label(self, text="Métricas", bg=CARD, fg=INK,
                 font=(FONT, 11, "bold")).grid(
            row=0, column=0, columnspan=_PER_ROW, sticky="w",
            padx=10, pady=(8, 2))

        self._values: dict[str, tk.Label] = {}
        for n, (key, title) in enumerate(_TILES):
            row, col = divmod(n, _PER_ROW)
            cell = tk.Frame(self, bg="#f8fafc", highlightbackground=BORDER,
                            highlightthickness=1)
            cell.grid(row=1 + row, column=col, sticky="nsew", padx=4, pady=4)
            value = tk.Label(cell, text="-", bg="#f8fafc", fg=INK,
                             font=(FONT, 14, "bold"))
            value.pack(pady=(6, 0))
            tk.Label(cell, text=title, bg="#f8fafc", fg=MUTED,
                     font=(FONT, 8), wraplength=96).pack(pady=(0, 6))
            self._values[key] = value
        for col in range(_PER_ROW):
            self.columnconfigure(col, weight=1, uniform="tile")

        style = ttk.Style()
        style.configure("Util.Horizontal.TProgressbar", background=BLUE,
                        troughcolor="#e2e8f0", borderwidth=0, thickness=14)
        bar_row = tk.Frame(self, bg=CARD)
        bar_row.grid(row=3, column=0, columnspan=_PER_ROW, sticky="ew",
                     padx=10, pady=(4, 0))
        tk.Label(bar_row, text="Utilización del recurso", bg=CARD, fg=INK,
                 font=(FONT, 10)).pack(side="left")
        self._util_text = tk.Label(bar_row, text="0 %", bg=CARD, fg=INK,
                                   font=(FONT, 10, "bold"))
        self._util_text.pack(side="right")
        self._bar = ttk.Progressbar(self, maximum=100, mode="determinate",
                                    style="Util.Horizontal.TProgressbar")
        self._bar.grid(row=4, column=0, columnspan=_PER_ROW, sticky="ew",
                       padx=10, pady=(2, 4))

        self._reasons = tk.Label(self, text="Motivos de salida: -", bg=CARD,
                                 fg=MUTED, font=(FONT, 9), anchor="w",
                                 justify="left", wraplength=520)
        self._reasons.grid(row=5, column=0, columnspan=_PER_ROW, sticky="ew",
                           padx=10, pady=(0, 8))

    def update_snapshot(self, snap) -> None:
        def num(key):
            return to_float(get_field(snap, key, 0))

        def whole(key):
            return str(int(num(key)))

        pairs = {
            "created": whole("created"),
            "served": whole("served"),
            "cancelled": whole("cancelled"),
            "errors": whole("errors"),
            "occupancy": f"{whole('occupied_now')} / {whole('max_occupied')}",
            "queue": f"{whole('waiting_now')} / {whole('max_queue')}",
            "avg_wait": f"{num('avg_wait'):.2f} s",
            "max_wait": f"{num('max_wait'):.2f} s",
            "avg_parking": f"{num('avg_parking'):.1f} s",
            "duration": fmt_clock(num("duration")),
        }
        for key, text in pairs.items():
            label = self._values[key]
            if label.cget("text") != text:
                label.configure(text=text)

        percent = to_percent(get_field(snap, "utilization", 0))
        self._bar["value"] = percent
        self._util_text.configure(text=f"{percent:.0f} %")

        reasons = get_field(snap, "exit_reasons", None)
        if isinstance(reasons, dict) and reasons:
            text = " · ".join(
                f"{EXIT_REASON_LABELS.get(k, k)} {v}"
                for k, v in reasons.items())
        else:
            text = "-"
        self._reasons.configure(text=f"Motivos de salida: {text}")
