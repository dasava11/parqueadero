"""Vista del parqueadero: semaforo, grilla de plazas, cola FIFO y tabla.

Solo dibuja. Nunca modifica el backend: recibe el parking_lot (para los
contadores) y la lista de vehiculos (copias) en cada refresh().

Para no parpadear ni gastar CPU, cada bloque calcula una "firma" del estado
que dibuja y solo toca los widgets cuando esa firma cambia.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from ui import theme
from ui.theme import (BG, BORDER, CARD, FONT, INK, MUTED, RED,
                      SCROLLBAR_STYLE, STATUS_STYLE, fmt_seconds, get_field, status_key,
                      style_of, vehicle_label)

# (apagado, encendido) para verde, amarillo y rojo
_LIGHTS = (("#bbf7d0", "#16a34a"), ("#fef08a", "#eab308"),
           ("#fecaca", "#dc2626"))
_FREE_FILL = "#f8fafc"
_FREE_OUTLINE = "#cbd5e1"


def _card(master, **pack):
    frame = tk.Frame(master, bg=CARD, highlightbackground=BORDER,
                     highlightthickness=1)
    frame.pack(**pack)
    return frame


class ParkingView(tk.Frame):
    """Semaforo + contadores + grilla de plazas + cola de espera."""

    COLS = 5
    CELL_W = 118
    CELL_H = 72
    PAD = 10
    MAX_CHIPS = 10

    def __init__(self, master, on_vehicle_click=None):
        super().__init__(master, bg=BG)
        self._on_vehicle_click = on_vehicle_click
        self._capacity = 0
        self._cols = self.COLS
        self._slots: list[dict] = []
        self._slot_vehicle: list = []
        self._slot_sig = None
        self._queue_sig = None
        self._base = theme.SPACE_NUMBER_BASE
        self._was_full = False
        self._tick = 0
        self._build_status_card()
        self._build_grid_card()
        self._build_queue_card()

    # ------------------------------------------------------------ construccion
    def _build_status_card(self) -> None:
        card = _card(self, fill="x", pady=(0, 10))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=14, pady=(12, 6))

        box = tk.Frame(top, bg=CARD)
        box.pack(side="left", padx=(0, 18))
        self._light = tk.Canvas(box, width=132, height=44, bg=CARD,
                                highlightthickness=0)
        self._light.pack()
        self._lamps = []
        for i, (dim, _lit) in enumerate(_LIGHTS):
            cx = 22 + i * 44
            self._lamps.append(self._light.create_oval(
                cx - 16, 6, cx + 16, 38, fill=dim, outline="#94a3b8", width=2))
        tk.Label(box, text="Semáforo del parqueadero", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack()

        self._free_value = self._tile(top, "Libres")
        self._busy_value = self._tile(top, "Ocupadas")
        self._queue_value = self._tile(top, "En cola")

        self._banner = tk.Label(card, text="", font=(FONT, 11, "bold"),
                                pady=6)
        self._banner.pack(fill="x", padx=14, pady=(0, 12))

    @staticmethod
    def _tile(parent, title: str) -> tk.Label:
        frame = tk.Frame(parent, bg=CARD)
        frame.pack(side="left", padx=14)
        value = tk.Label(frame, text="0", bg=CARD, fg=INK,
                         font=(FONT, 24, "bold"))
        value.pack()
        tk.Label(frame, text=title, bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack()
        return value

    def _build_grid_card(self) -> None:
        card = _card(self, fill="x", pady=(0, 10))
        tk.Label(card, text="Plazas (clic en una ocupada para seleccionarla)",
                 bg=CARD, fg=INK, font=(FONT, 11, "bold")
                 ).pack(anchor="w", padx=14, pady=(8, 0))
        self._canvas = tk.Canvas(card, bg=CARD, highlightthickness=0,
                                 width=self.COLS * self.CELL_W + 2 * self.PAD,
                                 height=self.CELL_H + 2 * self.PAD)
        self._canvas.pack(padx=4, pady=(0, 8))
        self._canvas.bind("<Button-1>", self._on_click)

    def _build_queue_card(self) -> None:
        card = _card(self, fill="x")
        self._queue_title = tk.Label(card, text="Cola de espera (FIFO)",
                                     bg=CARD, fg=INK, font=(FONT, 11, "bold"))
        self._queue_title.pack(anchor="w", padx=14, pady=(10, 0))
        self._queue_canvas = tk.Canvas(card, bg=CARD, height=46,
                                       highlightthickness=0)
        self._queue_canvas.pack(fill="x", padx=4, pady=(0, 8))

    # ------------------------------------------------------------------ API
    def update_view(self, lot, vehicles, selected_id=None) -> None:
        self._tick += 1
        snap = lot.get_snapshot()
        occupied = snap["occupied_count"]
        free = snap["available_count"]
        waiting = snap["waiting_count"]
        full = snap["is_full"]
        capacity = snap["capacity"]

        self._update_status(occupied, free, waiting, full, capacity)
        self._update_slots(capacity, snap["spaces"], selected_id)   # snap["spaces"], no vehicles
        self._update_queue(lot, vehicles, waiting)                  # igual que antes

    # ------------------------------------------------------------- semaforo
    def _update_status(self, occupied, free, waiting, full, capacity) -> None:
        self._free_value.configure(text=str(free))
        self._busy_value.configure(text=str(occupied))
        self._queue_value.configure(text=str(waiting))

        if full:
            lit = 2
            flash = (self._tick // 4) % 2 == 0  # parpadeo ~0.4 s
            self._banner.configure(
                text="¡Parqueadero lleno! Los vehículos esperan en la cola",
                bg=RED if flash else "#991b1b", fg="white")
        elif free <= max(1, round(capacity * 0.2)):
            lit = 1
            self._banner.configure(text="Quedan pocas plazas",
                                   bg="#fef9c3", fg="#854d0e")
        else:
            lit = 0
            self._banner.configure(text="Hay plazas disponibles",
                                   bg="#dcfce7", fg="#166534")

        for i, lamp in enumerate(self._lamps):
            self._light.itemconfigure(
                lamp, fill=_LIGHTS[i][1] if i == lit else _LIGHTS[i][0])

        if full and not self._was_full:  # alerta sonora solo al llenarse
            try:
                self.bell()
            except tk.TclError:
                pass
        self._was_full = full

    @staticmethod
    def _capacity_of(lot, occupied, free) -> int:
        cap = getattr(lot, "capacity", None)
        if isinstance(cap, int) and cap > 0:
            return cap
        try:
            size = len(lot.spaces)
            if size > 0:
                return size
        except Exception:
            pass
        return max(occupied + free, 1)

    # ---------------------------------------------------------------- grilla
    def _ensure_grid(self, capacity: int) -> None:
        if capacity == self._capacity and self._slots:
            return
        self._canvas.delete("all")
        self._slots = []
        self._slot_sig = None
        self._capacity = capacity
        self._cols = max(1, min(capacity, self.COLS if capacity <= 40 else 8))
        rows = math.ceil(capacity / self._cols)
        self._canvas.configure(
            width=2 * self.PAD + self._cols * self.CELL_W,
            height=2 * self.PAD + rows * self.CELL_H)

        c = self._canvas
        for i in range(capacity):
            row, col = divmod(i, self._cols)
            x = self.PAD + col * self.CELL_W
            y = self.PAD + row * self.CELL_H
            rect = c.create_rectangle(x + 4, y + 4, x + self.CELL_W - 4,
                                      y + self.CELL_H - 4, width=2)
            number = c.create_text(x + 12, y + 9, anchor="nw",
                                   font=(FONT, 8, "bold"), fill=MUTED)
            roof = c.create_rectangle(x + 44, y + 15, x + 74, y + 26, width=0)
            body = c.create_rectangle(x + 34, y + 25, x + 84, y + 43, width=0)
            w1 = c.create_oval(x + 40, y + 39, x + 50, y + 49, width=0,
                               fill=INK)
            w2 = c.create_oval(x + 68, y + 39, x + 78, y + 49, width=0,
                               fill=INK)
            text = c.create_text(x + self.CELL_W / 2, y + self.CELL_H - 13,
                                 font=(FONT, 10, "bold"))
            self._slots.append({"rect": rect, "num": number, "roof": roof,
                                "body": body, "w1": w1, "w2": w2,
                                "text": text})

    def _detect_base(self, spaces, capacity) -> None:
        if self._base is not None:
            return
        if 0 in spaces:
            self._base = 0
        elif capacity in spaces:
            self._base = 1

    def _update_slots(self, capacity, spaces, selected_id) -> None:
        self._ensure_grid(capacity)
        base = 1 if self._base is None else self._base   # el backend numera desde 1

        slot_vehicle = list(spaces)   # índice i = plaza base + i
        self._slot_vehicle = slot_vehicle

        sig = (base, selected_id, tuple(
            None if v is None else (get_field(v, "vehicle_id"),
                                    status_key(get_field(v, "status")))
            for v in slot_vehicle))
        if sig == self._slot_sig:
            return
        self._slot_sig = sig

        c = self._canvas
        for i, v in enumerate(slot_vehicle):
            it = self._slots[i]
            c.itemconfigure(it["num"], text=f"P{base + i}")
            if v is None:
                c.itemconfigure(it["rect"], fill=_FREE_FILL,
                                outline=_FREE_OUTLINE, width=2, dash=(4, 3))
                for part in ("roof", "body", "w1", "w2"):
                    c.itemconfigure(it[part], state="hidden")
                c.itemconfigure(it["text"], text="Libre", fill=MUTED)
                continue
            _label, bg, fg, strong = style_of(get_field(v, "status"))
            chosen = selected_id is not None and \
                get_field(v, "vehicle_id") == selected_id
            c.itemconfigure(it["rect"], fill=bg,
                            outline=INK if chosen else strong,
                            width=3 if chosen else 2, dash="")
            for part in ("roof", "body"):
                c.itemconfigure(it[part], fill=strong, state="normal")
            for part in ("w1", "w2"):
                c.itemconfigure(it[part], state="normal")
            c.itemconfigure(it["text"],
                            text=vehicle_label(get_field(v, "vehicle_id")),
                            fill=fg)

    def _on_click(self, event) -> None:
        col = (event.x - self.PAD) // self.CELL_W
        row = (event.y - self.PAD) // self.CELL_H
        if col < 0 or col >= self._cols or row < 0:
            return
        idx = row * self._cols + col
        if 0 <= idx < len(self._slot_vehicle):
            vehicle = self._slot_vehicle[idx]
            if vehicle is not None and self._on_vehicle_click:
                self._on_vehicle_click(get_field(vehicle, "vehicle_id"))

    # ------------------------------------------------------------------ cola
    @staticmethod
    def _waiting_ids(lot, vehicles) -> list:
        try:
            ids = []
            for item in list(lot.waiting_vehicles):
                vid = item if isinstance(item, (int, str)) \
                    else get_field(item, "vehicle_id")
                if vid is not None:
                    ids.append(vid)
            return ids
        except Exception:
            return [get_field(v, "vehicle_id") for v in vehicles
                    if status_key(get_field(v, "status")) == "waiting"]

    def _update_queue(self, lot, vehicles, waiting) -> None:
        ids = self._waiting_ids(lot, vehicles)
        sig = (tuple(ids[:self.MAX_CHIPS]), len(ids))
        if sig == self._queue_sig:
            return
        self._queue_sig = sig
        self._queue_title.configure(
            text=f"Cola de espera (FIFO): {len(ids)} vehículo(s)")

        c = self._queue_canvas
        c.delete("all")
        if not ids:
            c.create_text(12, 23, anchor="w", fill=MUTED, font=(FONT, 10),
                          text="Cola vacía")
            return
        for k, vid in enumerate(ids[:self.MAX_CHIPS]):
            x = 8 + k * 58
            first = k == 0
            c.create_rectangle(x, 8, x + 52, 38, width=2,
                               fill="#f59e0b" if first else "#fef3c7",
                               outline="#d97706")
            c.create_text(x + 26, 23, text=vehicle_label(vid),
                          font=(FONT, 9, "bold"),
                          fill="white" if first else "#92400e")
        extra = len(ids) - self.MAX_CHIPS
        if extra > 0:
            c.create_text(8 + self.MAX_CHIPS * 58, 23, anchor="w",
                          text=f"+{extra}", font=(FONT, 10, "bold"),
                          fill="#92400e")


class VehicleTable(tk.Frame):
    """Lista de vehiculos con su estado actual (color por estado)."""

    MAX_ROWS = 300
    COLUMNS = (("id", "ID", 50), ("status", "Estado", 96),
               ("space", "Plaza", 50), ("arrival", "Llegada", 66),
               ("stay", "Permanencia", 104), ("wait", "Espera", 66))

    def __init__(self, master):
        super().__init__(master, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1)
        header = tk.Frame(self, bg=CARD)
        header.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(header, text="Vehículos", bg=CARD, fg=INK,
                 font=(FONT, 11, "bold")).pack(side="left")
        self._hide_done = tk.BooleanVar(value=True)
        tk.Checkbutton(header, text="Ocultar finalizados y cancelados",
                       variable=self._hide_done, command=self._reset_rows,
                       bg=CARD, activebackground=CARD, fg=MUTED,
                       font=(FONT, 9)).pack(side="right")

        body = tk.Frame(self, bg=CARD)
        body.pack(fill="both", expand=True, padx=(10, 4), pady=(0, 10))
        self._tree = ttk.Treeview(body, columns=[c[0] for c in self.COLUMNS],
                                  show="headings", selectmode="browse",
                                  height=5)
        scroll = ttk.Scrollbar(body, orient="vertical",
                               style=SCROLLBAR_STYLE,
                               command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        for key, title, width in self.COLUMNS:
            self._tree.heading(key, text=title)
            self._tree.column(key, width=width, anchor="center",
                              stretch=key == "status")
        for key, (_l, bg, fg, _s) in STATUS_STYLE.items():
            self._tree.tag_configure(key, background=bg, foreground=fg)
        self._tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._rows: dict[str, tuple] = {}
        self._ids: dict[str, object] = {}

    def _reset_rows(self) -> None:
        children = self._tree.get_children()
        if children:
            self._tree.delete(*children)
        self._rows.clear()
        self._ids.clear()

    def update_rows(self, vehicles) -> None:
        hide = self._hide_done.get()
        wanted = []
        for v in vehicles:
            key = status_key(get_field(v, "status"))
            if hide and key in ("finished", "cancelled"):
                continue
            wanted.append((v, key))
        wanted = wanted[-self.MAX_ROWS:]

        keep = set()
        for v, key in wanted:
            vid = get_field(v, "vehicle_id")
            iid = str(vid)
            keep.add(iid)
            space = get_field(v, "parking_space")
            values = (vehicle_label(vid), style_of(key)[0],
                      "-" if space is None else f"P{space}",
                      fmt_seconds(get_field(v, "arrival_delay")),
                      fmt_seconds(get_field(v, "parking_duration")),
                      fmt_seconds(get_field(v, "waiting_time")))
            cached = self._rows.get(iid)
            if cached is None:
                self._tree.insert("", "end", iid=iid, values=values,
                                  tags=(key,))
                self._ids[iid] = vid
            elif cached != (values, key):
                self._tree.item(iid, values=values, tags=(key,))
            self._rows[iid] = (values, key)

        stale = [iid for iid in self._rows if iid not in keep]
        if stale:
            self._tree.delete(*stale)
            for iid in stale:
                del self._rows[iid]
                self._ids.pop(iid, None)

    def selected_id(self):
        selection = self._tree.selection()
        return self._ids.get(selection[0]) if selection else None

    def select(self, vehicle_id) -> None:
        iid = str(vehicle_id)
        if self._tree.exists(iid):
            self._tree.selection_set(iid)
            self._tree.see(iid)