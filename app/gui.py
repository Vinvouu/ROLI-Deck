"""Interface Tkinter : grille des zones, éditeur d'action, profils et pages."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, messagebox, simpledialog, ttk

from . import actions
from .model import N_COLS, N_ROWS, Config, Page, Profile, Zone, zone_key
from .pad import PadController

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

COLOR_PRESETS: list[tuple[str, str]] = [
    ("Rouge", "#ff0000"), ("Orange", "#ff6a00"), ("Jaune", "#ffd000"), ("Vert", "#00ff00"),
    ("Vert foncé", "#00a000"), ("Cyan", "#00c8c8"), ("Bleu", "#2b7cff"), ("Bleu foncé", "#0000a0"),
    ("Violet", "#b000ff"), ("Rose", "#ff2ad4"), ("Blanc", "#ffffff"), ("Gris", "#404040"),
    ("Éteint", "#000000"),
]
CUSTOM_LABEL = "Personnalisée…"

CELL = 96
CELL_GAP = 14


def _contrast(hex_color: str) -> str:
    s = hex_color.lstrip("#")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Lightpad — pad de commandes")
        root.minsize(900, 560)

        self.config = Config.load(CONFIG_PATH)
        self.selected: tuple[int, int] = (0, 0)
        self.param_widgets: dict[str, tk.Widget] = {}
        self._connecting = False
        self.auto_reconnect = True
        self._poll_ticks = 0

        self.pad = PadController(
            get_page=lambda: self.config.page,
            on_zone_press=self._on_zone_press,
            feedback_enabled=lambda: self.config.feedback,
            feedback_amount=lambda: self.config.feedback_amount,
            debounce_ms=lambda: self.config.debounce_ms,
        )

        self._build()
        self.refresh_all()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(300, self._auto_connect)
        root.after(500, self._poll)

    # ------------------------------------------------------------------ construction

    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Profil :").pack(side="left")
        self.profile_var = tk.StringVar()
        self.profile_cb = ttk.Combobox(top, textvariable=self.profile_var, state="readonly", width=18)
        self.profile_cb.pack(side="left", padx=(4, 2))
        self.profile_cb.bind("<<ComboboxSelected>>", lambda _e: self._select_profile())
        ttk.Button(top, text="+", width=3, command=self._add_profile).pack(side="left")
        ttk.Button(top, text="Renommer", command=self._rename_profile).pack(side="left", padx=2)
        ttk.Button(top, text="−", width=3, command=self._delete_profile).pack(side="left")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(top, text="Page :").pack(side="left")
        self.page_var = tk.StringVar()
        self.page_cb = ttk.Combobox(top, textvariable=self.page_var, state="readonly", width=16)
        self.page_cb.pack(side="left", padx=(4, 2))
        self.page_cb.bind("<<ComboboxSelected>>", lambda _e: self._select_page())
        ttk.Button(top, text="+", width=3, command=self._add_page).pack(side="left")
        ttk.Button(top, text="Renommer", command=self._rename_page).pack(side="left", padx=2)
        ttk.Button(top, text="−", width=3, command=self._delete_page).pack(side="left")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)

        self.feedback_var = tk.BooleanVar(value=self.config.feedback)
        ttk.Checkbutton(top, text="Retour visuel à l'appui", variable=self.feedback_var,
                        command=self._toggle_feedback).pack(side="left")

        self.connect_btn = ttk.Button(top, text="Connecter le pad", command=self._toggle_connect)
        self.connect_btn.pack(side="right")

        body = ttk.Frame(self.root, padding=8)
        body.pack(fill="both", expand=True)

        # --- grille
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        size_w = N_COLS * CELL + (N_COLS + 1) * CELL_GAP
        size_h = N_ROWS * CELL + (N_ROWS + 1) * CELL_GAP
        self.canvas = tk.Canvas(left, width=size_w, height=size_h, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        ttk.Label(left, text="Clique sur une zone pour l'éditer. L'orientation est celle du pad "
                            "(bouton en bas).", wraplength=size_w, foreground="#666").pack(pady=(6, 0))

        # --- éditeur
        right = ttk.LabelFrame(body, text="Zone", padding=10)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.editor = right

        ttk.Label(right, text="Libellé :").grid(row=0, column=0, sticky="w", pady=2)
        self.label_var = tk.StringVar()
        ttk.Entry(right, textvariable=self.label_var, width=36).grid(row=0, column=1, sticky="we", pady=2)

        ttk.Label(right, text="Action :").grid(row=1, column=0, sticky="w", pady=2)
        self.action_var = tk.StringVar()
        self.action_cb = ttk.Combobox(right, textvariable=self.action_var, state="readonly",
                                      values=[a.label for a in actions.ACTION_TYPES], width=40)
        self.action_cb.grid(row=1, column=1, sticky="we", pady=2)
        self.action_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_action_change())

        self.params_frame = ttk.Frame(right)
        self.params_frame.grid(row=2, column=0, columnspan=2, sticky="we", pady=(6, 2))

        ttk.Label(right, text="Couleur :").grid(row=3, column=0, sticky="w", pady=2)
        color_row = ttk.Frame(right)
        color_row.grid(row=3, column=1, sticky="we", pady=2)
        self.color_var = tk.StringVar()
        self.color_cb = ttk.Combobox(color_row, textvariable=self.color_var, state="readonly",
                                     values=[n for n, _ in COLOR_PRESETS] + [CUSTOM_LABEL], width=18)
        self.color_cb.pack(side="left")
        self.color_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_color_choice())
        self.color_hex = "#000000"
        self.swatch = tk.Label(color_row, width=6, relief="sunken", bg=self.color_hex)
        self.swatch.pack(side="left", padx=6)
        self.swatch_hex = ttk.Label(color_row, text=self.color_hex)
        self.swatch_hex.pack(side="left")

        btns = ttk.Frame(right)
        btns.grid(row=4, column=0, columnspan=2, sticky="we", pady=(12, 4))
        ttk.Button(btns, text="Enregistrer la zone", command=self._save_zone).pack(side="left")
        ttk.Button(btns, text="Tester l'action", command=self._test_zone).pack(side="left", padx=6)
        ttk.Button(btns, text="Effacer la zone", command=self._clear_zone).pack(side="left")
        right.columnconfigure(1, weight=1)

        # --- barre d'état
        status = ttk.Frame(self.root, padding=(8, 4))
        status.pack(fill="x", side="bottom")
        self.pad_status = ttk.Label(status, text="")
        self.pad_status.pack(side="left")
        self.msg = ttk.Label(status, text="", foreground="#06c")
        self.msg.pack(side="right")

    # ------------------------------------------------------------------ rafraîchissement

    def refresh_all(self) -> None:
        cfg = self.config
        self.profile_cb["values"] = [p.name for p in cfg.profiles]
        self.profile_var.set(cfg.profile.name)
        self.page_cb["values"] = [p.name for p in cfg.profile.pages]
        self.page_var.set(cfg.page.name)
        self.feedback_var.set(cfg.feedback)
        self.draw_grid()
        self.load_editor()

    def draw_grid(self) -> None:
        self.canvas.delete("all")
        page = self.config.page
        for r in range(N_ROWS):
            for c in range(N_COLS):
                x0 = CELL_GAP + c * (CELL + CELL_GAP)
                y0 = CELL_GAP + r * (CELL + CELL_GAP)
                z = page.get(c, r)
                color = z.color if z and not z.is_empty else "#000000"
                outline = "#ffffff" if (c, r) == self.selected else "#555"
                width = 3 if (c, r) == self.selected else 1
                self.canvas.create_rectangle(x0, y0, x0 + CELL, y0 + CELL, fill=color,
                                             outline=outline, width=width)
                if z and not z.is_empty:
                    text = z.label or actions.ACTIONS_BY_ID[z.action].label
                    self.canvas.create_text(x0 + CELL / 2, y0 + CELL / 2, text=text,
                                            fill=_contrast(color), width=CELL - 8,
                                            font=("Segoe UI", 9, "bold"), justify="center")
                else:
                    self.canvas.create_text(x0 + CELL / 2, y0 + CELL / 2, text=f"{c + 1},{r + 1}",
                                            fill="#444", font=("Segoe UI", 8))

    def load_editor(self) -> None:
        c, r = self.selected
        self.editor["text"] = f"Zone colonne {c + 1}, ligne {r + 1}"
        z = self.config.page.get(c, r) or Zone()
        self.label_var.set(z.label)
        t = actions.ACTIONS_BY_ID.get(z.action, actions.ACTIONS_BY_ID["none"])
        self.action_var.set(t.label)
        self._set_color(z.color)
        self._build_params(t, z.params)

    def _build_params(self, t: actions.ActionType, values: dict) -> None:
        for w in self.params_frame.winfo_children():
            w.destroy()
        self.param_widgets.clear()
        row = 0
        for prm in t.params:
            ttk.Label(self.params_frame, text=prm.label + " :").grid(row=row, column=0, sticky="nw", pady=2)
            val = str(values.get(prm.key, prm.default))
            if prm.kind == "multiline":
                txt = tk.Text(self.params_frame, width=44, height=5, font=("Consolas", 10))
                txt.insert("1.0", val)
                txt.grid(row=row, column=1, sticky="we", pady=2)
                self.param_widgets[prm.key] = txt
            else:
                var = tk.StringVar(value=val)
                ent = ttk.Entry(self.params_frame, textvariable=var, width=46)
                ent.grid(row=row, column=1, sticky="we", pady=2)
                self.param_widgets[prm.key] = ent
            row += 1
            if prm.hint:
                ttk.Label(self.params_frame, text=prm.hint, foreground="#777", wraplength=360,
                          justify="left").grid(row=row, column=1, sticky="w", pady=(0, 4))
                row += 1
        self.params_frame.columnconfigure(1, weight=1)

    def _read_params(self) -> dict:
        out: dict = {}
        for key, w in self.param_widgets.items():
            if isinstance(w, tk.Text):
                out[key] = w.get("1.0", "end").rstrip("\n")
            else:
                out[key] = w.get()  # type: ignore[union-attr]
        return out

    def _set_color(self, hex_color: str) -> None:
        self.color_hex = hex_color.lower()
        name = next((n for n, h in COLOR_PRESETS if h == self.color_hex), CUSTOM_LABEL)
        self.color_var.set(name)
        self.swatch.configure(bg=self.color_hex)
        self.swatch_hex.configure(text=self.color_hex)

    # ------------------------------------------------------------------ éditeur : événements

    def _on_canvas_click(self, ev: tk.Event) -> None:
        for r in range(N_ROWS):
            for c in range(N_COLS):
                x0 = CELL_GAP + c * (CELL + CELL_GAP)
                y0 = CELL_GAP + r * (CELL + CELL_GAP)
                if x0 <= ev.x <= x0 + CELL and y0 <= ev.y <= y0 + CELL:
                    self.selected = (c, r)
                    self.draw_grid()
                    self.load_editor()
                    return

    def _on_action_change(self) -> None:
        t = actions.ACTIONS_BY_LABEL[self.action_var.get()]
        z = self.config.page.get(*self.selected)
        self._build_params(t, z.params if z and z.action == t.id else {})
        # Couleur proposée par défaut si la zone n'en a pas encore une personnalisée.
        previous = actions.ACTIONS_BY_ID[z.action].default_color if z else "#000000"
        if self.color_hex in ("#000000", previous):
            self._set_color(t.default_color)

    def _on_color_choice(self) -> None:
        name = self.color_var.get()
        if name == CUSTOM_LABEL:
            _rgb, hex_color = colorchooser.askcolor(color=self.color_hex, title="Couleur de la zone")
            if hex_color:
                self._set_color(hex_color)
            else:
                self._set_color(self.color_hex)
            return
        self._set_color(dict(COLOR_PRESETS)[name])

    def _save_zone(self) -> None:
        t = actions.ACTIONS_BY_LABEL[self.action_var.get()]
        c, r = self.selected
        if t.id == "none":
            self.config.page.clear(c, r)
        else:
            self.config.page.zones[zone_key(c, r)] = Zone(
                label=self.label_var.get().strip(),
                action=t.id,
                params=self._read_params(),
                color=self.color_hex,
            )
        self.save()
        self.draw_grid()
        self.pad.render()
        self.flash("Zone enregistrée.")

    def _clear_zone(self) -> None:
        self.config.page.clear(*self.selected)
        self.save()
        self.refresh_all()
        self.pad.render()

    def _test_zone(self) -> None:
        t = actions.ACTIONS_BY_LABEL[self.action_var.get()]
        zone = Zone(label=self.label_var.get(), action=t.id, params=self._read_params(), color=self.color_hex)
        delay = 2000 if t.keyboard else 0
        if delay:
            self.flash("Bascule vers la fenêtre cible : exécution dans 2 s…")
        self.root.after(delay, lambda: self._run(zone))

    # ------------------------------------------------------------------ profils / pages

    def _select_profile(self) -> None:
        self.config.set_profile(self.profile_cb.current())
        self._after_nav()

    def _select_page(self) -> None:
        self.config.set_page(self.page_cb.current())
        self._after_nav()

    def _add_profile(self) -> None:
        name = simpledialog.askstring("Nouveau profil", "Nom du profil :", parent=self.root)
        if name:
            self.config.profiles.append(Profile(name=name.strip()))
            self.config.set_profile(len(self.config.profiles) - 1)
            self._after_nav()

    def _rename_profile(self) -> None:
        name = simpledialog.askstring("Renommer le profil", "Nouveau nom :",
                                      initialvalue=self.config.profile.name, parent=self.root)
        if name:
            self.config.profile.name = name.strip()
            self._after_nav()

    def _delete_profile(self) -> None:
        if len(self.config.profiles) <= 1:
            messagebox.showinfo("Profil", "Il faut garder au moins un profil.")
            return
        if messagebox.askyesno("Supprimer", f"Supprimer le profil « {self.config.profile.name} » ?"):
            del self.config.profiles[self.config.active_profile]
            self.config.set_profile(0)
            self._after_nav()

    def _add_page(self) -> None:
        name = simpledialog.askstring("Nouvelle page", "Nom de la page :",
                                      initialvalue=f"Page {len(self.config.profile.pages) + 1}", parent=self.root)
        if name:
            self.config.profile.pages.append(Page(name=name.strip()))
            self.config.set_page(len(self.config.profile.pages) - 1)
            self._after_nav()

    def _rename_page(self) -> None:
        name = simpledialog.askstring("Renommer la page", "Nouveau nom :",
                                      initialvalue=self.config.page.name, parent=self.root)
        if name:
            self.config.page.name = name.strip()
            self._after_nav()

    def _delete_page(self) -> None:
        if len(self.config.profile.pages) <= 1:
            messagebox.showinfo("Page", "Il faut garder au moins une page.")
            return
        if messagebox.askyesno("Supprimer", f"Supprimer la page « {self.config.page.name} » ?"):
            del self.config.profile.pages[self.config.active_page]
            self.config.set_page(0)
            self._after_nav()

    def _after_nav(self) -> None:
        self.save()
        self.refresh_all()
        self.pad.render()

    def _toggle_feedback(self) -> None:
        self.config.feedback = self.feedback_var.get()
        self.save()
        self.pad.render()

    # ------------------------------------------------------------------ navigation (depuis le pad)

    def next_page(self) -> None:
        self.config.next_page()
        self._nav_from_pad()

    def prev_page(self) -> None:
        self.config.prev_page()
        self._nav_from_pad()

    def goto_page(self, index: int) -> None:
        self.config.set_page(index)
        self._nav_from_pad()

    def goto_profile(self, name: str) -> None:
        if not self.config.set_profile_by_name(name):
            raise ValueError(f"profil introuvable : {name}")
        self._nav_from_pad()

    def _nav_from_pad(self) -> None:
        self.pad.render()
        self.root.after(0, self._after_nav)

    # ------------------------------------------------------------------ exécution des actions

    def _on_zone_press(self, col: int, row: int) -> None:
        """Appelé depuis le thread du pad : exécute l'action dans un thread dédié."""
        zone = self.config.page.get(col, row)
        if zone is None or zone.is_empty:
            return
        threading.Thread(target=self._run, args=(zone,), daemon=True).start()

    def _run(self, zone: Zone) -> None:
        try:
            actions.run_action(zone, nav=self)
            text = f"Action exécutée : {zone.label or actions.describe(zone)}"
        except Exception as exc:  # noqa: BLE001
            text = f"Échec de l'action « {zone.label} » : {exc}"
        self.root.after(0, lambda: self.flash(text))

    # ------------------------------------------------------------------ pad

    def _auto_connect(self) -> None:
        """Tentative de connexion en arrière-plan (une seule à la fois)."""
        if self._connecting or self.pad.connected:
            return
        self._connecting = True

        def work() -> None:
            try:
                self.pad.connect()
            finally:
                self._connecting = False

        threading.Thread(target=work, daemon=True).start()

    def _toggle_connect(self) -> None:
        if self.pad.connected:
            self.auto_reconnect = False
            threading.Thread(target=self.pad.disconnect, daemon=True).start()
        else:
            self.auto_reconnect = True
            self._auto_connect()

    def _poll(self) -> None:
        # Reconnexion automatique toutes les 5 s tant que le pad est absent
        # (pad en veille, câble rebranché…), sauf déconnexion volontaire.
        self._poll_ticks += 1
        if self.auto_reconnect and not self.pad.connected and self._poll_ticks % 10 == 0:
            self._auto_connect()
        text = self.pad.status
        if self.pad.connected and self.pad.info:
            text += f" — {self.pad.info}"
        if self.pad.last_press is not None:
            c, r = self.pad.last_press
            text += f" — dernier appui : zone {c + 1},{r + 1}"
        self.pad_status.configure(text=text)
        self.connect_btn.configure(text="Déconnecter le pad" if self.pad.connected else "Connecter le pad")
        self.root.after(500, self._poll)

    # ------------------------------------------------------------------ divers

    def save(self) -> None:
        self.config.save(CONFIG_PATH)

    def flash(self, text: str) -> None:
        self.msg.configure(text=text)
        self.root.after(5000, lambda: self.msg.configure(text="") if self.msg.cget("text") == text else None)

    def on_close(self) -> None:
        self.save()
        try:
            self.pad.disconnect()
        finally:
            self.root.destroy()


def run() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
