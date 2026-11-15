# ROLI Deck — pad de commandes pour Lightpad BLOCK

Transforme un ROLI Lightpad BLOCK en pad de commandes façon Stream Deck : 16 zones
(grille 4 × 4 de carrés de 3 × 3 LEDs séparés d'une LED), chacune assignée à une
action et à une couleur, organisées en profils et pages.

## Lancer

```bash
python main.py
```

Prérequis : Python 3.11, `python-rtmidi` (voir `requirements.txt`). Le pad doit être
branché en USB et aucun logiciel ROLI (Dashboard, BLOCKS Code) ne doit tourner.
L'application se connecte au pad toute seule et réessaie toutes les 5 s.

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

## Structure

```
main.py                 point d'entrée
app/model.py            zones, pages, profils, config JSON, géométrie de la grille
app/actions.py          catalogue des actions et exécution
app/winput.py           simulation clavier Windows (SendInput)
app/pad.py              liaison avec le pad : LEDs, touchers, retour visuel
app/gui.py              interface Tkinter
roli_blocks/            protocole BLOCKS en pur Python (SysEx), voir NOTICE.md
prototypes/             prototype de validation des LEDs
docs/faisabilite_leds.md  étude de faisabilité du pilotage des LEDs
tests/                  tests unitaires du protocole (python -m pytest)
```

## Comment les LEDs sont pilotées

Le pad passe en « mode API » (protocole BLOCKS de ROLI, en SysEx sur le port MIDI
USB). On lui envoie un petit programme LittleFoot « bitmap » déjà compilé, puis les
15 × 15 pixels en RGB565. Pendant ce temps le pad n'émet plus de notes MIDI :
l'application reçoit les touchers bruts et calcule la zone. À la fermeture, le pad
revient à son programme habituel. Détails dans `docs/faisabilite_leds.md`.
