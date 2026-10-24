"""Ventana principal. Cumple el contrato que usa main.py:

    MainWindow(root, controller, metrics)
    on_event(evento)   -> una vez por evento, en el hilo principal
    refresh()          -> una vez por ciclo de after()

Los botones llaman directamente al controller y muestran como alerta los
RuntimeError, ValueError y TimeoutError. La ventana nunca toca hilos: solo
lee copias (controller.vehicles, parking_lot, metrics.snapshot()).
"""
from __future__ import annotations

import time
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

from ui.control_panel import ControlPanel
from ui.metrics_board import MetricsBoard
from ui.parking_view import ParkingView, VehicleTable
from ui.theme import (BG, BLUE, BORDER, CARD, FONT, HEADER, INK, MUTED, RED,
                      SCROLLBAR_STYLE, configure_scrollbar_style, get_field,
                      read_flag, status_key, vehicle_label)

_EVENT_TEXT = {
    "simulation_started": "Simulación iniciada",
    "vehicle_created": "{v} creado",
    "vehicle_approaching": "{v} se acerca al parqueadero",
    "vehicle_waiting": "{v} espera una plaza",
    "vehicle_parked": "{v} se estacionó en la plaza {space}",
    "vehicle_exit_requested": "Salida solicitada para {v}",
    "vehicle_exiting": "{v} está saliendo",
    "vehicle_finished": "{v} terminó",
    "vehicle_cancelled": "{v} cancelado",
    "vehicle_error": "Error en {v}",
    "simulation_stopping": "Deteniendo la simulación…",
    "generation_stopped": "Generación automática detenida",
    "simulation_reset": "Simulación reiniciada",
    "controller_error": "Error del controlador",
    "controller_shutdown": "Controlador cerrado",
}
_ERRORS = {"vehicle_error", "controller_error"}


class MainWindow:
    LOG_MAX_LINES = 300

    def __init__(self, root, controller, metrics):
        self.root = root
        self.controller = controller
        self.metrics = metrics
        self._reported: set[str] = set()
        self._build_style()
        self._build_layout()
        self._log("Interfaz lista. Pulsa «Iniciar simulación».", MUTED)

    # ---------------------------------------------------------- construccion
    def _build_style(self) -> None:
        self.root.title("Simulador de parqueadero inteligente")
        self.root.geometry("1260x780")
        self.root.minsize(1120, 700)
        self.root.configure(bg=BG)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24, background=CARD,
                        fieldbackground=CARD, borderwidth=0,
                        font=(FONT, 10))
        style.configure("Treeview.Heading", background=HEADER,
                        foreground="white", font=(FONT, 10, "bold"),
                        relief="flat")
        configure_scrollbar_style(style)
        style.map("Treeview.Heading", background=[("active", "#334155")])
        # Arreglo conocido de Tk 8.6.9: sin esto los colores por tag no se ven.
        style.map("Treeview",
                  background=[("selected", "#1d4ed8")] + self._fixed_map(
                      style, "background"),
                  foreground=[("selected", "white")] + self._fixed_map(
                      style, "foreground"))

    @staticmethod
    def _fixed_map(style, option):
        return [e for e in style.map("Treeview", query_opt=option)
                if e[:2] != ("!disabled", "!selected")]

    def _build_layout(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = tk.Frame(root, bg=HEADER)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="Simulador de parqueadero inteligente",
                 bg=HEADER, fg="white", font=(FONT, 16, "bold")
                 ).pack(side="left", padx=18, pady=10)
        tk.Label(header, text="Cada vehículo es un hilo; las plazas son un "
                 "recurso con semáforo", bg=HEADER, fg="#cbd5e1",
                 font=(FONT, 10)).pack(side="left", pady=10)

        self.control_panel = ControlPanel(
            root, on_start=self._on_start, on_add=self._on_add,
            on_stop=self._on_stop, on_reset=self._on_reset,
            on_exit=self._on_exit)
        self.control_panel.grid(row=1, column=0, sticky="ew", padx=14,
                                pady=(10, 8))

        body = tk.Frame(root, bg=BG)
        body.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self.parking_view = ParkingView(
            body, on_vehicle_click=self._on_slot_click)
        self.parking_view.grid(row=0, column=0, sticky="nsw", padx=(0, 12))

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        self.metrics_board = MetricsBoard(right)
        self.metrics_board.pack(fill="x")
        self.vehicle_table = VehicleTable(right)

        log = tk.Frame(right, bg=CARD, highlightbackground=BORDER,
                       highlightthickness=1)
        log.pack(side="bottom", fill="x", pady=(10, 0))
        tk.Label(log, text="Registro de eventos", bg=CARD, fg=INK,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=10,
                                               pady=(6, 0))
        box = tk.Frame(log, bg=CARD)
        box.pack(fill="x", padx=(10, 4), pady=(2, 8))
        self._log_list = tk.Listbox(box, height=5, bg=CARD, bd=0,
                                    highlightthickness=0, activestyle="none",
                                    font=(FONT, 9), selectbackground=CARD)
        scroll = ttk.Scrollbar(box, orient="vertical",
                               style=SCROLLBAR_STYLE,
                               command=self._log_list.yview)
        self._log_list.configure(yscrollcommand=scroll.set)
        self._log_list.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")

        # La tabla se empaqueta al final: usa el espacio que sobra y nunca
        # empuja el registro fuera de la ventana.
        self.vehicle_table.pack(fill="both", expand=True, pady=(10, 0))

    # -------------------------------------------------------------- acciones
    def _guard(self, action, *args):
        """Ejecuta una accion del controller y muestra sus errores esperables."""
        try:
            return action(*args)
        except (RuntimeError, ValueError, TimeoutError) as exc:
            messagebox.showwarning("Acción no permitida", str(exc),
                                   parent=self.root)
            self._log(f"Aviso: {exc}", RED)
            return None

    def _on_start(self) -> None:
        self._guard(self.controller.start_simulation)

    def _on_add(self) -> None:
        self._guard(self.controller.add_manual_vehicle)

    def _on_stop(self) -> None:
        self._guard(self.controller.stop_simulation)

    def _on_reset(self) -> None:
        self._guard(self.controller.reset_simulation)

    def _on_exit(self) -> None:
        vehicle_id = self.vehicle_table.selected_id()
        if vehicle_id is None:
            messagebox.showinfo("Salida manual",
                                "Selecciona un vehículo estacionado.",
                                parent=self.root)
            return
        self._guard(self.controller.request_vehicle_exit, vehicle_id)

    def _on_slot_click(self, vehicle_id) -> None:
        self.vehicle_table.select(vehicle_id)

    # ---------------------------------------------------------------- eventos
    def on_event(self, event) -> None:
        etype = get_field(event, "event_type") or get_field(event, "type") \
            or "evento"
        vid = get_field(event, "vehicle_id")
        space = get_field(event, "parking_space")
        if space is None:
            space = get_field(event, "released_space")
        template = _EVENT_TEXT.get(etype, etype)
        try:
            text = template.format(
                v=vehicle_label(vid) if vid is not None else "",
                space="?" if space is None else space)
        except (KeyError, IndexError, ValueError):
            text = str(etype)

        if etype in _ERRORS:
            color = RED
        elif etype == "vehicle_parked":
            color = BLUE
        elif etype in ("vehicle_finished", "vehicle_exiting"):
            color = MUTED
        elif etype == "vehicle_cancelled":
            color = "#b45309"
        else:
            color = INK
        self._log(text, color)

    def _log(self, text: str, color: str = INK) -> None:
        lst = self._log_list
        lst.insert("end", f"{time.strftime('%H:%M:%S')}  {text}")
        lst.itemconfigure("end", foreground=color)
        extra = lst.size() - self.LOG_MAX_LINES
        if extra > 0:
            lst.delete(0, extra - 1)
        lst.see("end")

    # ---------------------------------------------------------------- refresco
    def refresh(self) -> None:
        vehicles = self._vehicles()
        self._safe("vista", lambda: self.parking_view.update_view(
            self.controller.parking_lot, vehicles,
            self.vehicle_table.selected_id()))
        self._safe("tabla", lambda: self.vehicle_table.update_rows(vehicles))
        self._safe("métricas", lambda: self.metrics_board.update_snapshot(
            self.metrics.snapshot()))
        self._safe("botones", lambda: self._update_buttons(vehicles))

    def _update_buttons(self, vehicles) -> None:
        selected = self.vehicle_table.selected_id()
        can_exit = any(
            get_field(v, "vehicle_id") == selected
            and status_key(get_field(v, "status")) == "parked"
            for v in vehicles) if selected is not None else False
        self.control_panel.set_state(
            running=read_flag(self.controller, "is_running"),
            idle=read_flag(self.controller, "is_idle"),
            can_exit=can_exit)

    def _vehicles(self) -> list:
        raw = self.controller.vehicles
        if callable(raw):
            raw = raw()
        if isinstance(raw, dict):
            raw = raw.values()
        return list(raw)

    def _safe(self, name: str, action) -> None:
        """Aisla fallos de dibujo: un bloque roto no deja a los demas sin pintar."""
        try:
            action()
        except Exception:
            if name not in self._reported:
                self._reported.add(name)
                print(f"[ui] error al actualizar «{name}»:")
                traceback.print_exc()