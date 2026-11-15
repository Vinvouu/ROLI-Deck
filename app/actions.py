"""Catalogue des actions assignables à une zone et leur exécution.

Chaque type d'action déclare : un identifiant, un libellé (menu déroulant), une
couleur par défaut, la liste de ses paramètres (champ de saisie dans l'éditeur)
et si elle doit être différée lors d'un test depuis l'interface (actions clavier,
qui visent la fenêtre active).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import winput
from .model import Zone


class Navigator(Protocol):
    """Ce que les actions de navigation peuvent demander à l'application."""

    def next_page(self) -> None: ...
    def prev_page(self) -> None: ...
    def goto_page(self, index: int) -> None: ...
    def goto_profile(self, name: str) -> None: ...


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str = "text"  # "text" | "multiline"
    default: str = ""
    hint: str = ""


@dataclass(frozen=True)
class ActionType:
    id: str
    label: str
    default_color: str
    params: tuple[Param, ...] = ()
    keyboard: bool = False  # vise la fenêtre active → test différé
    group: str = ""


ACTION_TYPES: list[ActionType] = [
    ActionType("none", "— Aucune —", "#000000"),
    ActionType("launch", "Lancer une application / fichier / URL", "#2b7cff", (
        Param("target", "Cible", "text", "chrome",
              "Nom d'une application connue (chrome, notepad, explorer), chemin d'un .exe, "
              "d'un fichier, d'un dossier ou une adresse web"),
        Param("args", "Arguments (optionnel)", "text", "", "Arguments de ligne de commande"),
    ), group="Système"),
    ActionType("url", "Ouvrir une adresse web", "#1aa3ff", (
        Param("url", "Adresse", "text", "https://"),
    ), group="Système"),
    ActionType("command", "Exécuter une commande", "#8080ff", (
        Param("cmd", "Commande", "text", "", "Exécutée dans un interpréteur de commandes"),
    ), group="Système"),
    ActionType("volume_up", "Volume +", "#00ff00", (
        Param("steps", "Crans", "text", "2", "Chaque cran vaut 2 % de volume"),
    ), group="Son"),
    ActionType("volume_down", "Volume −", "#00a000", (
        Param("steps", "Crans", "text", "2"),
    ), group="Son"),
    ActionType("mute", "Muet / rétablir le son", "#a00000", group="Son"),
    ActionType("play_pause", "Lecture / pause", "#ffffff", group="Son"),
    ActionType("next_track", "Piste suivante", "#c0c0c0", group="Son"),
    ActionType("prev_track", "Piste précédente", "#808080", group="Son"),
    ActionType("hotkey", "Raccourci clavier", "#ff6a00", (
        Param("keys", "Touches", "text", "ctrl+c", "Exemples : ctrl+c, ctrl+shift+esc, win+d, alt+f4, f5"),
    ), keyboard=True, group="Clavier"),
    ActionType("text", "Saisir du texte", "#ffd000", (
        Param("text", "Texte", "multiline", "", "Saisi tel quel dans la fenêtre active"),
    ), keyboard=True, group="Clavier"),
    ActionType("macro", "Macro (séquence d'étapes)", "#b000ff", (
        Param("steps", "Étapes (une par ligne)", "multiline", "touche ctrl+c\nattendre 0.2\ntouche ctrl+v",
              "Mots-clés : touche <combo>, texte <texte>, attendre <secondes>, lancer <cible>, "
              "url <adresse>, commande <cmd>, media <volume_up|volume_down|mute|play_pause|next|prev>"),
    ), keyboard=True, group="Clavier"),
    ActionType("page_next", "Page suivante", "#404040", group="Navigation"),
    ActionType("page_prev", "Page précédente", "#404040", group="Navigation"),
    ActionType("page_goto", "Aller à la page n°", "#606060", (
        Param("index", "Numéro de page (1 = première)", "text", "1"),
    ), group="Navigation"),
    ActionType("profile_goto", "Changer de profil", "#606060", (
        Param("name", "Nom du profil", "text", "Défaut"),
    ), group="Navigation"),
]

ACTIONS_BY_ID: dict[str, ActionType] = {a.id: a for a in ACTION_TYPES}
ACTIONS_BY_LABEL: dict[str, ActionType] = {a.label: a for a in ACTION_TYPES}


# --------------------------------------------------------------------------- exécution


def launch(target: str, args: str = "") -> None:
    target = target.strip()
    if not target:
        raise ValueError("cible vide")
    if target.lower().startswith(("http://", "https://")):
        webbrowser.open(target)
        return
    exe = shutil.which(target)
    if args.strip():
        subprocess.Popen([exe or target, *shlex.split(args, posix=False)])
        return
    if exe:
        subprocess.Popen([exe])
        return
    # ShellExecute résout les applications enregistrées (chrome, msedge…),
    # les fichiers, dossiers et adresses.
    os.startfile(target)  # type: ignore[attr-defined]


def run_macro(steps: str, nav: Navigator | None = None) -> None:
    for raw in steps.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        word = word.lower()
        rest = rest.strip()
        if word in ("touche", "key", "hotkey"):
            winput.send_hotkey(rest)
        elif word in ("texte", "text", "type"):
            winput.send_text(rest)
        elif word in ("attendre", "wait", "sleep"):
            time.sleep(float(rest.replace(",", ".") or "0"))
        elif word in ("lancer", "launch", "run", "ouvrir", "open"):
            launch(rest)
        elif word == "url":
            webbrowser.open(rest)
        elif word in ("commande", "command", "cmd"):
            subprocess.Popen(rest, shell=True)
        elif word in ("media", "média"):
            winput.media_key(rest.lower())
        elif word in ("page",) and nav is not None:
            if rest in ("suivante", "next"):
                nav.next_page()
            elif rest in ("précédente", "precedente", "prev"):
                nav.prev_page()
            else:
                nav.goto_page(int(rest) - 1)
        else:
            raise ValueError(f"étape inconnue : {line!r}")


def run_action(zone: Zone, nav: Navigator | None = None) -> None:
    """Exécute l'action d'une zone. Lève une exception en cas d'échec."""
    p = zone.params
    a = zone.action
    if a == "none":
        return
    if a == "launch":
        launch(p.get("target", ""), p.get("args", ""))
    elif a == "url":
        webbrowser.open(p.get("url", ""))
    elif a == "command":
        subprocess.Popen(p.get("cmd", ""), shell=True)
    elif a == "volume_up":
        winput.media_key("volume_up", int(p.get("steps", "2") or 1))
    elif a == "volume_down":
        winput.media_key("volume_down", int(p.get("steps", "2") or 1))
    elif a == "mute":
        winput.media_key("mute")
    elif a == "play_pause":
        winput.media_key("play_pause")
    elif a == "next_track":
        winput.media_key("next")
    elif a == "prev_track":
        winput.media_key("prev")
    elif a == "hotkey":
        winput.send_hotkey(p.get("keys", ""))
    elif a == "text":
        winput.send_text(p.get("text", ""))
    elif a == "macro":
        run_macro(p.get("steps", ""), nav)
    elif a == "page_next" and nav:
        nav.next_page()
    elif a == "page_prev" and nav:
        nav.prev_page()
    elif a == "page_goto" and nav:
        nav.goto_page(int(p.get("index", "1")) - 1)
    elif a == "profile_goto" and nav:
        nav.goto_profile(p.get("name", ""))
    else:
        raise ValueError(f"action inconnue : {a}")


def describe(zone: Zone) -> str:
    """Résumé court d'une zone pour l'interface."""
    t = ACTIONS_BY_ID.get(zone.action)
    if t is None or t.id == "none":
        return ""
    first = next((zone.params.get(prm.key, "") for prm in t.params), "")
    first = str(first).splitlines()[0] if first else ""
    return f"{t.label}" + (f" : {first}" if first else "")
