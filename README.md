# ROLI Deck — pad de commandes pour Lightpad BLOCK

Transforme un ROLI Lightpad BLOCK en pad de commandes façon Stream Deck : 16 zones
(grille 4 × 4 de carrés de 3 × 3 LEDs séparés d'une LED), chacune assignée à une
action et à une couleur, organisées en profils et pages.

## Lancer

```bash
python main.py
```

Prérequis : Python 3.11 et les bibliothèques de `requirements.txt`
(`pip install -r requirements.txt`). Aucun logiciel ROLI (Dashboard, BLOCKS Code)
ne doit tourner. Le pad se connecte en USB ou en Bluetooth (voir plus bas) ; en
USB, l'application le détecte toute seule.

## Utilisation

1. Clique sur une zone de la grille.
2. Choisis une action dans le menu déroulant, remplis ses paramètres, choisis une
   couleur (préréglée ou personnalisée), puis « Enregistrer la zone ».
3. La zone s'allume sur le pad ; un appui déclenche l'action. Avec « Retour visuel
   à l'appui », la zone s'éclaircit tant que le doigt est posé.
4. Les profils et pages se gèrent dans la barre du haut. Une zone peut elle-même
   changer de page ou de profil.

La configuration est enregistrée automatiquement dans `config.json`.

## Actions

| Action | Paramètres |
|---|---|
| Lancer une application / fichier / URL | cible (`chrome`, `notepad`, chemin, dossier, adresse), arguments |
| Ouvrir une adresse web | adresse |
| Exécuter une commande | ligne de commande |
| Volume +, Volume − | nombre de crans (2 % par cran) |
| Muet, Lecture/pause, Piste suivante/précédente | — |
| Raccourci clavier | `ctrl+c`, `ctrl+shift+esc`, `win+d`, `alt+f4`, `f5`… |
| Saisir du texte | texte tel quel |
| Macro | une étape par ligne, voir ci-dessous |
| Page suivante / précédente / n°, Changer de profil | numéro ou nom |

Les actions clavier visent la fenêtre active. Depuis « Tester l'action », elles
partent avec 2 s de délai pour laisser le temps de cliquer sur la fenêtre cible.

### Syntaxe des macros

```
touche ctrl+c
attendre 0.2
touche ctrl+v
texte Bonjour
lancer notepad
url https://example.org
commande echo ok
media volume_up
page suivante
```

## Bluetooth

Le Lightpad se connecte aussi en Bluetooth Low Energy, **sans l'appairer dans
Windows** : l'application se connecte directement au pad (bibliothèque `bleak`).
Windows 11 n'a pas de pilote BLE MIDI, donc l'appairage par les Paramètres
Windows ne sert à rien et gêne même la connexion directe.

1. Pad allumé et **débranché de l'USB** (branché, il n'annonce pas le Bluetooth).
2. Bouton « Connecter en Bluetooth… » : l'application cherche 6 s, liste les
   Lightpad trouvés avec la force du signal, puis connexion en un clic.
3. Si le pad est appairé dans Windows, l'application propose de retirer cet
   appairage (rien n'est fait sans confirmation).
4. Le pad choisi est mémorisé dans `config.json` : au démarrage et après chaque
   coupure, l'application s'y reconnecte toute seule, avec un délai progressif
   (1, 2, 4, 8, 15 puis 30 s). « Oublier ce pad » efface cette mémorisation.

Règles de priorité : **l'USB passe avant le Bluetooth**. Si le pad est branché
pendant une liaison Bluetooth, l'application bascule sur l'USB ; au
débranchement, elle revient au Bluetooth mémorisé. À chaque (re)connexion les
couleurs de la page active sont renvoyées au pad.

L'indicateur d'état (point coloré en haut, et icône dans la barre des tâches)
montre : gris déconnecté, jaune recherche, vert USB, bleu Bluetooth, avec le
niveau de batterie. Fermer la fenêtre réduit l'application dans la barre des
tâches ; « Quitter » est dans le menu de l'icône.

### Dépannage Bluetooth

| Message | Cause probable et remède |
|---|---|
| Le Bluetooth est désactivé dans Windows | Active-le dans les paramètres rapides (Win + A). |
| Pad introuvable en Bluetooth | Pad éteint, trop loin, **branché en USB**, ou déjà connecté à un téléphone / une tablette. Déconnecte-le de l'autre appareil. |
| Le pad ne répond pas à la connexion (délai dépassé) | Souvent un appairage Windows résiduel : Paramètres > Bluetooth > supprimer le pad, ou laisser l'application le retirer. Éteindre puis rallumer le pad aide aussi. |
| Se connecte puis se coupe aussitôt | Symptôme typique de l'appairage Windows. Retire l'appairage, puis reconnecte depuis l'application. |
| Le pad est branché en USB | Comportement voulu : l'USB est prioritaire. Débranche pour passer en Bluetooth. |

Le journal détaillé est dans `roli_deck.log` à côté de `config.json` (rotation à
1 Mo). Pour diagnostiquer plus finement, les scripts autonomes de
`tools/ble_diagnostics/` (scan, connexion directe, durée, reconnexion, appairage,
LEDs) ont chacun leur mode d'emploi dans `tools/ble_diagnostics/README.md`.

## Exécutable Windows (PyInstaller)

```bash
pyinstaller roli_deck.spec --noconfirm
```

Produit `dist/ROLI-Deck/ROLI-Deck.exe` (dossier à copier tel quel). Le fichier
`roli_deck.spec` inclut explicitement les paquets `winrt-*` et le backend Windows
de `bleak`, ainsi que `pystray`. Pour vérifier qu'ils sont bien embarqués :

```bash
dist\ROLI-Deck\ROLI-Deck.exe --selftest
```

écrit `selftest.txt` à côté de l'exécutable avec une ligne par dépendance et
`RESULT OK`. `config.json` et `roli_deck.log` sont créés à côté de l'exécutable.

## Structure

```
main.py                 point d'entrée (--selftest : vérifie les dépendances)
app/model.py            zones, pages, profils, config JSON, géométrie de la grille
app/actions.py          catalogue des actions et exécution
app/winput.py           simulation clavier Windows (SendInput)
app/pad.py              gestionnaire de liaison USB/Bluetooth, LEDs, touchers, reconnexion
app/ble.py              worker Bluetooth (bleak + WinRT) : scan, appairage, connexion
app/gui.py              interface Tkinter (grille, éditeur, dialogue Bluetooth)
app/tray.py             icône de la barre des tâches
app/logsetup.py         journal roli_deck.log
roli_blocks/            protocole BLOCKS en pur Python (SysEx), voir NOTICE.md
roli_blocks/ble_midi.py       codec BLE MIDI (paquets, timestamps, SysEx fragmenté)
roli_blocks/ble_transport.py  transport BLOCKS sur BLE, même contrat que l'USB
tools/ble_diagnostics/  scripts de diagnostic Bluetooth autonomes
prototypes/             prototype de validation des LEDs
docs/faisabilite_leds.md  étude de faisabilité du pilotage des LEDs
tests/                  tests unitaires (python -m pytest) : protocole, codec BLE, politique USB/BLE
roli_deck.spec          recette PyInstaller
```

## Comment les LEDs sont pilotées

Le pad passe en « mode API » (protocole BLOCKS de ROLI, en SysEx sur le port MIDI
USB ou, en Bluetooth, sur la caractéristique BLE MIDI avec fragmentation des
SysEx). On lui envoie un petit programme LittleFoot « bitmap » déjà compilé, puis
les 15 × 15 pixels en RGB565. Pendant ce temps le pad n'émet plus de notes MIDI :
l'application reçoit les touchers bruts et calcule la zone. À la fermeture, le pad
revient à son programme habituel. Détails dans `docs/faisabilite_leds.md`.
