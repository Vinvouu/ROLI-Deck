# Diagnostics Bluetooth (phase 1)

Scripts autonomes pour tester la liaison Bluetooth Low Energy avec le Lightpad
BLOCK, sans rien changer à l'application. Ils utilisent `bleak` (pip) et les
paquets `winrt-*` qu'il installe. Chaque exécution écrit aussi un journal dans
`tools/ble_diagnostics/logs/`, à joindre à tes retours.

Toujours lancer depuis la racine du projet :

```bash
python -m tools.ble_diagnostics.diag01_scan
```

Préparation du pad : allumé, **débranché de l'USB**, et non connecté à un autre
appareil (téléphone, tablette). Tant qu'il est branché en USB, il n'annonce pas
le Bluetooth.

Options communes aux scripts 2 à 6 : `--address 48:B6:20:12:D7:E2` (adresse du
pad vue au scan, évite la recherche par nom) et `--scan-timeout 10`.

## 1. Scan — `diag01_scan`

```bash
python -m tools.ble_diagnostics.diag01_scan --duration 8
```

À observer : une ligne `LIGHTPAD: name='Lightpad Block I3CQ' address=... rssi=...`.
Le service annoncé `03b80e5a` est le service BLE MIDI. Un RSSI vers −50 dBm est
excellent, vers −80 dBm faible.

## 2. Connexion directe sans appairage — `diag02_connect`

```bash
python -m tools.ble_diagnostics.diag02_connect --duration 60
```

À observer : `connected in x s, MTU=23`, la liste des services (dont MIDI), puis
`subscribed`. Touche le pad : chaque message décodé s'affiche (`Note On ch1
note=66 vel=9`, puis `Note Off`). À la fin, `still connected after 60 s`. Si la
ligne `DISCONNECTED by the pad or Windows` apparaît avant, note l'heure et ce que
tu faisais.

## 3. Test de durée — `diag03_duration`

```bash
python -m tools.ble_diagnostics.diag03_duration --minutes 5
python -m tools.ble_diagnostics.diag03_duration --minutes 5 --keepalive 20
```

Ne touche pas le pad pendant le test. À observer : une ligne `still connected`
toutes les 30 s, puis `SUCCESS`. Si le lien tombe tout seul, la ligne `link
dropped after N s idle` donne le délai d'inactivité ; relancer alors avec
`--keepalive 20` (lecture GATT toutes les 20 s) pour voir si ça le maintient.

## 4. Reconnexion automatique — `diag04_reconnect`

```bash
python -m tools.ble_diagnostics.diag04_reconnect --cycles 2
```

Une fois `connected` affiché, éteins le pad, attends 5 s, rallume-le. À observer :
`DISCONNECTED`, des tentatives espacées de 1, 2, 4, 8, 15, 30 s, puis
`RECONNECTED #1 after N attempt(s)`. Refaire une fois ; le script s'arrête
après 2 reconnexions réussies.

## 5. Appairage Windows — `diag05_pairing`

```bash
python -m tools.ble_diagnostics.diag05_pairing
python -m tools.ble_diagnostics.diag05_pairing --unpair
```

À observer : la liste des appareils BLE appairés dans Windows et la ligne
`pad ...: known_to_windows=... paired=...`. Avec `--unpair`, le script demande
une confirmation (`oui`) avant de retirer l'appairage, puis réaffiche l'état.
Pour reproduire le problème d'origine : appairer le pad dans les Paramètres
Windows, relancer le script 2 (observer si la connexion tient), puis retirer
l'appairage avec `--unpair` et relancer le script 2.

## 6. LEDs en Bluetooth — `diag06_leds`

```bash
python -m tools.ble_diagnostics.diag06_leds --duration 15 --feedback
```

À observer sur le pad : la zone en haut à gauche en rouge ; avec `--feedback`,
chaque zone touchée s'éclaircit pendant l'appui. Dans la console : `topology in
0.03 s`, `program uploaded`, `full frame sent in ~65 ms`, `ACKs received`, et
`rx_bad: 0` dans les statistiques finales (aucune trame SysEx corrompue par la
fragmentation). `--delay 0.005` ajoute une pause entre paquets si des trames se
perdent.

## Résultats déjà obtenus (16 novembre 2026, Lightpad Block M, firmware 1.1.0)

| Test | Résultat |
|---|---|
| 1 Scan | pad vu à −50 dBm, service MIDI annoncé |
| 2 Connexion directe | connexion en 1,8 s sans appairage, notes décodées, stable 25 s |
| 5 Appairage | pad connu de Windows mais **non appairé** au moment du test |
| 6 LEDs | protocole BLOCKS complet en BLE : trame complète en 60–80 ms, 0 erreur, zone rouge et retour visuel confirmés à l'œil |
| 3 Durée | 3 min sans toucher le pad, sans keep-alive : aucune coupure |
| 4 Reconnexion, 5 avec appairage Windows | à faire par l'utilisateur (pad en main) |
