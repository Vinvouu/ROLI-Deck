"""Prototype minimal : allumer une zone du Lightpad BLOCK en rouge.

Objectif : valider sur le pad physique que l'on peut piloter les LEDs depuis
Python (python-rtmidi + protocole BLOCKS en SysEx), sans ROLI Dashboard ni
utilitaire C++.

Déroulement :
  1. détection du pad sur le bus MIDI (ports entrée + sortie) ;
  2. demande de topologie (numéro de série, firmware, batterie) ;
  3. passage en « mode API » + ping toutes les 400 ms ;
  4. envoi du programme LittleFoot « bitmap » (107 octets) dans la mémoire du pad ;
  5. écriture des pixels : fond noir, une zone en rouge ;
  6. pendant l'affichage, les touchers sont affichés dans la console et
     (option --feedback) la zone touchée s'éclaircit ;
  7. extinction et sortie du mode API.

Usage :
    python prototypes/proto_zone_rouge.py                 # zone (0, 0) en rouge, 8 s
    python prototypes/proto_zone_rouge.py --zone 1 2      # colonne 1, ligne 2
    python prototypes/proto_zone_rouge.py --color 00ff00  # autre couleur
    python prototypes/proto_zone_rouge.py --dry-run       # sans pad : affiche les trames SysEx
    python prototypes/proto_zone_rouge.py --list-ports    # ports MIDI visibles
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

# Rend le paquet `roli_blocks` importable quel que soit le dossier courant.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roli_blocks import Block, BlockGroup, Color, Touch, find_block_port_pairs  # noqa: E402
from roli_blocks.led import LIGHTPAD_GRID_H, LIGHTPAD_GRID_W, LightpadGrid  # noqa: E402
from roli_blocks._bytecode import BITMAP_LED_PROGRAM_BYTECODE  # noqa: E402
from roli_blocks.protocol import sysex  # noqa: E402

# --------------------------------------------------------------------------- zones


def bands(total: int, n: int, gap: int) -> list[tuple[int, int]]:
    """Découpe `total` LEDs en `n` bandes quasi égales séparées par `gap` LEDs.

    Retourne une liste de (début, longueur). Pour 15 LEDs, 4 zones, gap 0 :
    [(0, 4), (4, 4), (8, 4), (12, 3)] — des zones « d'environ 4 LEDs ».
    """
    usable = total - gap * (n - 1)
    base, extra = divmod(usable, n)
    out: list[tuple[int, int]] = []
    pos = 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        out.append((pos, size))
        pos += size + gap
    return out


def zone_rect(col: int, row: int, n_cols: int, n_rows: int, gap: int) -> tuple[int, int, int, int]:
    """Rectangle (x, y, largeur, hauteur) en LEDs de la zone (col, row)."""
    xs = bands(LIGHTPAD_GRID_W, n_cols, gap)
    ys = bands(LIGHTPAD_GRID_H, n_rows, gap)
    x, w = xs[col]
    y, h = ys[row]
    return x, y, w, h


def zone_at(x_norm: float, y_norm: float, n_cols: int, n_rows: int, gap: int) -> tuple[int, int] | None:
    """Zone sous un toucher (coordonnées normalisées 0..1), ou None si dans un interstice."""
    px = min(int(x_norm * LIGHTPAD_GRID_W), LIGHTPAD_GRID_W - 1)
    py = min(int(y_norm * LIGHTPAD_GRID_H), LIGHTPAD_GRID_H - 1)
    col = next((i for i, (s, n) in enumerate(bands(LIGHTPAD_GRID_W, n_cols, gap)) if s <= px < s + n), None)
    row = next((i for i, (s, n) in enumerate(bands(LIGHTPAD_GRID_H, n_rows, gap)) if s <= py < s + n), None)
    if col is None or row is None:
        return None
    return col, row


def paint_zone(grid: LightpadGrid, rect: tuple[int, int, int, int], color: Color) -> None:
    x0, y0, w, h = rect
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            grid.set_pixel(x, y, color)


def lighten(c: Color, amount: float = 0.6) -> Color:
    """Éclaircit une couleur vers le blanc (retour visuel à l'appui)."""
    return Color(
        int(c.r + (255 - c.r) * amount),
        int(c.g + (255 - c.g) * amount),
        int(c.b + (255 - c.b) * amount),
    )


# --------------------------------------------------------------------------- dry run


def dry_run(rect: tuple[int, int, int, int], color: Color) -> None:
    """Construit les trames sans ouvrir de port MIDI et les affiche en hexadécimal."""
    grid = LightpadGrid(program_size=len(BITMAP_LED_PROGRAM_BYTECODE))
    paint_zone(grid, rect, color)
    payload = grid.encode_diff(packet_index=3)
    assert payload is not None
    frame = sysex.encode(0, payload, from_host=True)

    print(f"Programme LittleFoot bitmap : {len(BITMAP_LED_PROGRAM_BYTECODE)} octets")
    print(f"Zone {rect} en #{color.r:02x}{color.g:02x}{color.b:02x}")
    print(f"Trame SysEx 'sharedDataChange' (pixels) : {len(frame)} octets")
    print("  " + " ".join(f"{b:02X}" for b in frame[:24]) + " ...")
    print("  " + "... " + " ".join(f"{b:02X}" for b in frame[-8:]))
    hdr = frame[:5]
    assert hdr == bytes([0xF0, 0x00, 0x21, 0x10, 0x77]), "en-tête ROLI inattendu"
    assert frame[-1] == 0xF7
    decoded = sysex.decode(frame)
    assert decoded.payload == payload and not decoded.from_device
    print("Encodage / décodage SysEx cohérents (checksum OK).")


# --------------------------------------------------------------------------- live


def live(args: argparse.Namespace, rect: tuple[int, int, int, int], color: Color) -> int:
    pairs = find_block_port_pairs()
    if not pairs:
        print("Aucun Lightpad détecté sur le bus MIDI.")
        print("  - vérifie le câble USB (le pad doit apparaître comme périphérique MIDI) ;")
        print("  - ferme ROLI Dashboard / BLOCKS Code s'ils tournent (ils verrouillent le pad) ;")
        print("  - lance avec --list-ports pour voir les ports visibles.")
        return 1
    pair = pairs[0]
    print(f"Pad : entrée '{pair.in_name}'  sortie '{pair.out_name}'")

    with BlockGroup(pair) as group:
        blocks: list[Block] = []
        for attempt in range(3):
            try:
                blocks = group.fetch_topology(timeout=2.5)
                break
            except TimeoutError:
                print("  pas de réponse à la demande de topologie — touche le pad pour le réveiller…")
        if not blocks:
            print("Le pad ne répond pas au protocole BLOCKS.")
            return 2

        for b in blocks:
            print(f"  {b}  firmware={b.firmware_version or '?'}")
        lightpad = next((b for b in blocks if b.is_lightpad), None)
        if lightpad is None:
            print("Aucun Lightpad dans la topologie.")
            return 2

        # Suivi des touchers → console + retour visuel optionnel.
        lock = threading.Lock()
        active: dict[int, tuple[int, int]] = {}  # touch_index -> zone touchée

        def on_touch(blk: Block, t: Touch) -> None:
            kind = "début" if t.is_start else ("fin  " if t.is_end else "mouv.")
            z = zone_at(t.x_normalized, t.y_normalized, args.cols, args.rows, args.gap)
            print(
                f"  toucher {t.touch_index} {kind} x={t.x_normalized:.2f} y={t.y_normalized:.2f} "
                f"p={t.pressure:.2f} zone={z}"
            )
            if not args.feedback or blk.grid is None:
                return
            changed = False
            with lock:
                if t.is_start and z is not None:
                    active[t.touch_index] = z
                    changed = True
                elif t.is_end and t.touch_index in active:
                    del active[t.touch_index]
                    changed = True
                if changed:
                    blk.grid.fill(Color(0, 0, 0))
                    paint_zone(blk.grid, rect, color)
                    for zc in set(active.values()):
                        zr = zone_rect(zc[0], zc[1], args.cols, args.rows, args.gap)
                        base = color if zc == (args.zone[0], args.zone[1]) else Color(40, 40, 40)
                        paint_zone(blk.grid, zr, lighten(base))
            if changed:
                # Envoi complet + sans attendre l'inactivité : voir la note sur les
                # pertes de paquets pendant un toucher dans docs/faisabilite_leds.md.
                group.flush_grid(blk, full=True)

        group.on_touch(on_touch)

        print("Passage en mode API…")
        group.begin_api_mode()
        print("Envoi du programme LittleFoot bitmap…")
        group.load_bitmap_program(lightpad)
        time.sleep(0.5)

        print(f"Zone {args.zone} -> LEDs {rect} en #{color.r:02x}{color.g:02x}{color.b:02x}")
        group.fill(lightpad, Color(0, 0, 0))
        paint_zone(lightpad.grid, rect, color)  # type: ignore[arg-type]
        group.flush_grid(lightpad)

        print(f"Affichage pendant {args.duration:.0f} s — touche le pad pour voir les événements"
              + (" (et le retour visuel)" if args.feedback else "") + "…")
        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:
            pass

        print("Extinction…")
        group.fill(lightpad, Color(0, 0, 0))
        group.flush_grid(lightpad, full=True)
        time.sleep(0.3)

    print("Sortie du mode API (le pad doit revenir à son programme habituel).")
    return 0


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zone", nargs=2, type=int, default=[0, 0], metavar=("COL", "LIGNE"),
                    help="zone à allumer (colonne, ligne), à partir de 0 en haut à gauche")
    ap.add_argument("--cols", type=int, default=4, help="nombre de colonnes de zones (défaut 4)")
    ap.add_argument("--rows", type=int, default=4, help="nombre de lignes de zones (défaut 4)")
    ap.add_argument("--gap", type=int, default=0, help="LEDs d'interstice entre zones (défaut 0)")
    ap.add_argument("--color", default="ff0000", help="couleur hex RRGGBB (défaut ff0000 = rouge)")
    ap.add_argument("--duration", type=float, default=8.0, help="durée d'affichage en secondes")
    ap.add_argument("--feedback", action="store_true", help="éclaircir la zone touchée pendant l'appui")
    ap.add_argument("--dry-run", action="store_true", help="n'ouvre aucun port : affiche les trames")
    ap.add_argument("--list-ports", action="store_true", help="liste les ports MIDI et sort")
    args = ap.parse_args(argv)

    if args.list_ports:
        import rtmidi

        print("Entrées :", rtmidi.MidiIn().get_ports())
        print("Sorties :", rtmidi.MidiOut().get_ports())
        print("Paires BLOCKS :", find_block_port_pairs())
        return 0

    col, row = args.zone
    if not (0 <= col < args.cols and 0 <= row < args.rows):
        ap.error(f"--zone doit être dans 0..{args.cols - 1} x 0..{args.rows - 1}")
    rect = zone_rect(col, row, args.cols, args.rows, args.gap)
    color = Color.from_hex(args.color)

    if args.dry_run:
        dry_run(rect, color)
        return 0
    return live(args, rect, color)


if __name__ == "__main__":
    raise SystemExit(main())
