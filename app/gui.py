"""Tkinter GUI: zone grid, action editor, profiles/pages, USB + Bluetooth link status."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import colorchooser, messagebox, simpledialog, ttk

from . import actions
from .ble import BleError, FoundPad
from .model import N_COLS, N_ROWS, Config, Page, Profile, Zone, zone_key
from .pad import LinkState, PadController
from .paths import CONFIG_PATH

log = logging.getLogger(__name__)

COLOR_PRESETS: list[tuple[str, str]] = [
    ("Rouge", "#ff0000"), ("Orange", "#ff6a00"), ("Jaune", "#ffd000"), ("Vert", "#00ff00"),
    ("Vert foncé", "#00a000"), ("Cyan", "#00c8c8"), ("Bleu", "#2b7cff"), ("Bleu foncé", "#0000a0"),
    ("Violet", "#b000ff"), ("Rose", "#ff2ad4"), ("Blanc", "#ffffff"), ("Gris", "#404040"),
    ("Éteint", "#000000"),
]
CUSTOM_LABEL = "Personnalisée…"

CELL = 96
CELL_GAP = 14

STATE_DOT = {
    LinkState.DISCONNECTED: "#7a7a7a",
    LinkState.SEARCHING: "#e6b400",
    LinkState.USB: "#2ecc40",
    LinkState.BLE: "#2b7cff",
}


def _contrast(hex_color: str) -> str:
    s = hex_color.lstrip("#")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("ROLI Deck — pad de commandes")
        root.minsize(960, 600)

        self.config = Config.load(CONFIG_PATH)
        self.selected: tuple[int, int] = (0, 0)
        self.param_widgets: dict[str, tk.Widget] = {}
        self._ble_dialog: BluetoothDialog | None = None
        self._last_tray_key: tuple | None = None

        self.pad = PadController(
            get_page=lambda: self.config.page,
            on_zone_press=self._on_zone_press,
            feedback_enabled=lambda: self.config.feedback,
            feedback_amount=lambda: self.config.feedback_amount,
            debounce_ms=lambda: self.config.debounce_ms,
            get_ble_address=lambda: self.config.ble_address,
        )
        self.pad.on_state_change = lambda: self.root.after(0, self._update_status)

        self._build()
        self.tray = self._make_tray()
        self.refresh_all()
        root.protocol("WM_DELETE_WINDOW", self.on_close_button)
        self.pad.start()
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

        # --- link bar (USB / Bluetooth)
        link = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        link.pack(fill="x")
        self.dot = tk.Canvas(link, width=16, height=16, highlightthickness=0)
        self.dot.pack(side="left")
        self._dot_id = self.dot.create_oval(2, 2, 14, 14, fill=STATE_DOT[LinkState.DISCONNECTED], outline="")
        self.link_label = ttk.Label(link, text="", font=("Segoe UI", 9, "bold"))
        self.link_label.pack(side="left", padx=(6, 12))
        self.link_detail = ttk.Label(link, text="", foreground="#666")
        self.link_detail.pack(side="left", fill="x", expand=True)
        self.forget_btn = ttk.Button(link, text="Oublier ce pad", command=self._forget_ble)
        self.forget_btn.pack(side="right")
        self.disconnect_btn = ttk.Button(link, text="Déconnecter", command=self._toggle_disconnect)
        self.disconnect_btn.pack(side="right", padx=6)
        ttk.Button(link, text="Connecter en Bluetooth…", command=self.open_bluetooth_dialog).pack(side="right")

        body = ttk.Frame(self.root, padding=8)
        body.pack(fill="both", expand=True)

        # --- grid
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        size_w = N_COLS * CELL + (N_COLS + 1) * CELL_GAP
        size_h = N_ROWS * CELL + (N_ROWS + 1) * CELL_GAP
        self.canvas = tk.Canvas(left, width=size_w, height=size_h, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        ttk.Label(left, text="Clique sur une zone pour l'éditer. L'orientation est celle du pad "
                            "(bouton en bas).", wraplength=size_w, foreground="#666").pack(pady=(6, 0))

        # --- editor
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

        # --- status bar
        status = ttk.Frame(self.root, padding=(8, 4))
        status.pack(fill="x", side="bottom")
        self.pad_status = ttk.Label(status, text="")
        self.pad_status.pack(side="left")
        self.msg = ttk.Label(status, text="", foreground="#06c")
        self.msg.pack(side="right")

    def _make_tray(self):
        try:
            from .tray import TrayIcon

            return TrayIcon(
                on_show=lambda: self.root.after(0, self.show_window),
                on_bluetooth=lambda: self.root.after(0, lambda: (self.show_window(), self.open_bluetooth_dialog())),
                on_quit=lambda: self.root.after(0, self.quit),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("tray icon unavailable: %s", exc)
            return None

    # ------------------------------------------------------------------ refresh

    def refresh_all(self) -> None:
        cfg = self.config
        self.profile_cb["values"] = [p.name for p in cfg.profiles]
        self.profile_var.set(cfg.profile.name)
        self.page_cb["values"] = [p.name for p in cfg.profile.pages]
        self.page_var.set(cfg.page.name)
        self.feedback_var.set(cfg.feedback)
        self.draw_grid()
        self.load_editor()
        self._update_status()

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

    # ------------------------------------------------------------------ editor events

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
        previous = actions.ACTIONS_BY_ID[z.action].default_color if z else "#000000"
        if self.color_hex in ("#000000", previous):
            self._set_color(t.default_color)

    def _on_color_choice(self) -> None:
        name = self.color_var.get()
        if name == CUSTOM_LABEL:
            _rgb, hex_color = colorchooser.askcolor(color=self.color_hex, title="Couleur de la zone")
            self._set_color(hex_color or self.color_hex)
            return
        self._set_color(dict(COLOR_PRESETS)[name])

    def _save_zone(self) -> None:
        t = actions.ACTIONS_BY_LABEL[self.action_var.get()]
        c, r = self.selected
        if t.id == "none":
            self.config.page.clear(c, r)
        else:
            self.config.page.zones[zone_key(c, r)] = Zone(
                label=self.label_var.get().strip(), action=t.id,
                params=self._read_params(), color=self.color_hex,
            )
        self.save()
        self.draw_grid()
        self._render_async()
        self.flash("Zone enregistrée.")

    def _clear_zone(self) -> None:
        self.config.page.clear(*self.selected)
        self.save()
        self.refresh_all()
        self._render_async()

    def _test_zone(self) -> None:
        t = actions.ACTIONS_BY_LABEL[self.action_var.get()]
        zone = Zone(label=self.label_var.get(), action=t.id, params=self._read_params(), color=self.color_hex)
        delay = 2000 if t.keyboard else 0
        if delay:
            self.flash("Bascule vers la fenêtre cible : exécution dans 2 s…")
        self.root.after(delay, lambda: threading.Thread(target=self._run, args=(zone,), daemon=True).start())

    # ------------------------------------------------------------------ profiles / pages

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
        self._render_async()

    def _toggle_feedback(self) -> None:
        self.config.feedback = self.feedback_var.get()
        self.save()
        self._render_async()

    def _render_async(self) -> None:
        # LED frames go over USB or BLE; never block the GUI thread on them.
        threading.Thread(target=self.pad.render, daemon=True).start()

    # ------------------------------------------------------------------ navigation from the pad

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
        self.root.after(0, lambda: (self.save(), self.refresh_all()))

    # ------------------------------------------------------------------ actions

    def _on_zone_press(self, col: int, row: int) -> None:
        zone = self.config.page.get(col, row)
        if zone is None or zone.is_empty:
            return
        threading.Thread(target=self._run, args=(zone,), daemon=True).start()

    def _run(self, zone: Zone) -> None:
        try:
            actions.run_action(zone, nav=self)
            text = f"Action exécutée : {zone.label or actions.describe(zone)}"
        except Exception as exc:  # noqa: BLE001
            log.warning("action failed: %s", exc)
            text = f"Échec de l'action « {zone.label} » : {exc}"
        self.root.after(0, lambda: self.flash(text))

    # ------------------------------------------------------------------ link / Bluetooth

    def _update_status(self) -> None:
        state = self.pad.state
        self.dot.itemconfigure(self._dot_id, fill=STATE_DOT[state])
        label = state.label
        if state in (LinkState.USB, LinkState.BLE) and self.pad.battery is not None:
            label += f" · batterie {self.pad.battery} %" + (" (en charge)" if self.pad.charging else "")
        self.link_label.configure(text=label)
        detail = self.pad.message if state not in (LinkState.USB, LinkState.BLE) else self.pad.info
        if self.config.ble_address and state != LinkState.BLE:
            detail += f"   [pad Bluetooth mémorisé : {self.config.ble_name or self.config.ble_address}]"
        self.link_detail.configure(text=detail)
        self.forget_btn.state(["!disabled"] if self.config.ble_address else ["disabled"])
        self.disconnect_btn.configure(text="Reprendre" if self.pad._ble_suspended and not self.pad.connected
                                      else "Déconnecter")
        if self.tray is not None:
            key = (state, label)
            if key != self._last_tray_key:
                self._last_tray_key = key
                self.tray.update(state, label)

    def _poll(self) -> None:
        self._update_status()
        text = ""
        if self.pad.last_press is not None:
            c, r = self.pad.last_press
            text = f"Dernier appui : zone {c + 1},{r + 1}"
        self.pad_status.configure(text=text)
        self.root.after(500, self._poll)

    def _toggle_disconnect(self) -> None:
        if self.pad._ble_suspended and not self.pad.connected:
            self.pad.resume()
        else:
            threading.Thread(target=self.pad.disconnect, daemon=True).start()

    def _forget_ble(self) -> None:
        if not self.config.ble_address:
            return
        name = self.config.ble_name or self.config.ble_address
        if not messagebox.askyesno("Oublier ce pad", f"Ne plus se reconnecter automatiquement à « {name} » ?"):
            return
        self.config.ble_address = None
        self.config.ble_name = ""
        self.save()
        if self.pad.state == LinkState.BLE:
            threading.Thread(target=self.pad.disconnect, daemon=True).start()
        self.pad.resume()
        self._update_status()
        self.flash("Pad Bluetooth oublié.")

    def open_bluetooth_dialog(self) -> None:
        if self._ble_dialog is not None and self._ble_dialog.winfo_exists():
            self._ble_dialog.lift()
            return
        self._ble_dialog = BluetoothDialog(self)

    def ble_pad_chosen(self, pad: FoundPad) -> None:
        """Called by the dialog (on the Tk thread) once the user picked a pad."""

        def work() -> None:
            try:
                known, paired = self.pad.ble.pairing_state(pad.address)
                if paired:
                    answer = self._ask_on_main(
                        "Appairage Windows détecté",
                        f"« {pad.name} » est appairé dans Windows. Cet appairage empêche souvent la "
                        "connexion directe (le pad se connecte une seconde puis se coupe).\n\n"
                        "Retirer l'appairage Windows maintenant ?")
                    if answer:
                        status = self.pad.ble.unpair(pad.address)
                        self._flash_on_main(f"Appairage retiré ({status}).")
                self.config.ble_address = pad.address
                self.config.ble_name = pad.name
                self.root.after(0, self.save)
                self._flash_on_main(f"Connexion à {pad.name}…")
                self.pad.connect_ble(pad.address)
                self._flash_on_main(f"Connecté en Bluetooth à {pad.name}.")
                self.pad.request_ble_now()
            except BleError as exc:
                log.warning("bluetooth connect failed: %s", exc)
                self.root.after(0, lambda: messagebox.showerror("Bluetooth", str(exc)))
                self.pad.request_ble_now()
            except Exception as exc:  # noqa: BLE001
                log.exception("bluetooth connect crashed")
                self.root.after(0, lambda: messagebox.showerror("Bluetooth", f"Erreur inattendue : {exc}"))
            self.root.after(0, self._update_status)

        threading.Thread(target=work, daemon=True).start()

    def _ask_on_main(self, title: str, text: str) -> bool:
        done = threading.Event()
        result = {"value": False}

        def ask() -> None:
            result["value"] = messagebox.askyesno(title, text, parent=self.root)
            done.set()

        self.root.after(0, ask)
        done.wait()
        return result["value"]

    def _flash_on_main(self, text: str) -> None:
        self.root.after(0, lambda: self.flash(text))

    # ------------------------------------------------------------------ misc

    def save(self) -> None:
        self.config.save(CONFIG_PATH)

    def flash(self, text: str) -> None:
        self.msg.configure(text=text)
        self.root.after(6000, lambda: self.msg.configure(text="") if self.msg.cget("text") == text else None)

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close_button(self) -> None:
        """Closing the window keeps the pad running from the tray; Quit is in the tray menu."""
        self.save()
        if self.tray is not None:
            self.root.withdraw()
            self.tray.notify("ROLI Deck continue dans la barre des tâches. Clic droit sur l'icône pour quitter.")
        else:
            self.quit()

    def quit(self) -> None:
        self.save()
        try:
            self.pad.stop()
        finally:
            if self.tray is not None:
                self.tray.stop()
            self.root.destroy()


class BluetoothDialog(tk.Toplevel):
    """Scan for Lightpads, show them with signal strength, connect in one click."""

    def __init__(self, app: App) -> None:
        super().__init__(app.root)
        self.app = app
        self.title("Connecter en Bluetooth")
        self.resizable(False, False)
        self.transient(app.root)
        self.pads: list[FoundPad] = []

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.info = ttk.Label(frame, text="Le pad doit être allumé et débranché de l'USB.", wraplength=420)
        self.info.pack(anchor="w")
        self.status = ttk.Label(frame, text="", foreground="#06c")
        self.status.pack(anchor="w", pady=(4, 6))
        self.listbox = tk.Listbox(frame, width=60, height=6, font=("Segoe UI", 10))
        self.listbox.pack()
        self.listbox.bind("<Double-Button-1>", lambda _e: self._connect())
        btns = ttk.Frame(frame)
        btns.pack(fill="x", pady=(10, 0))
        self.scan_btn = ttk.Button(btns, text="Rechercher à nouveau", command=self._scan)
        self.scan_btn.pack(side="left")
        self.connect_btn = ttk.Button(btns, text="Connecter", command=self._connect)
        self.connect_btn.pack(side="right")
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right", padx=6)
        self._scan()

    def _scan(self) -> None:
        self.status.configure(text="Recherche en cours (6 s)…")
        self.listbox.delete(0, "end")
        self.scan_btn.state(["disabled"])
        self.connect_btn.state(["disabled"])

        def work() -> None:
            try:
                pads = self.app.pad.ble.scan(6.0)
                self.after(0, lambda: self._show(pads))
            except BleError as exc:
                self.after(0, lambda: self._fail(str(exc)))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._fail(f"Erreur inattendue : {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _show(self, pads: list[FoundPad]) -> None:
        if not self.winfo_exists():
            return
        self.pads = pads
        self.scan_btn.state(["!disabled"])
        if not pads:
            self.status.configure(text="Aucun Lightpad trouvé. Vérifie qu'il est allumé, débranché de l'USB "
                                       "et non connecté à un autre appareil.")
            return
        self.status.configure(text=f"{len(pads)} pad(s) trouvé(s). Sélectionne-en un puis Connecter.")
        for p in pads:
            self.listbox.insert("end", f"{p.name}   {p.address}   {p.signal_label} ({p.rssi} dBm)")
        self.listbox.selection_set(0)
        self.connect_btn.state(["!disabled"])

    def _fail(self, text: str) -> None:
        if not self.winfo_exists():
            return
        self.scan_btn.state(["!disabled"])
        self.status.configure(text=text)

    def _connect(self) -> None:
        sel = self.listbox.curselection()
        if not sel or not self.pads:
            return
        pad = self.pads[sel[0]]
        self.destroy()
        self.app.ble_pad_chosen(pad)


def run() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
