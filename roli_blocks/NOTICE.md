# Origine de ce paquet

Ce dossier est une copie adaptée du paquet `roli` du projet
**roli-blocks-py** (https://github.com/kairess/roli-blocks-py), un portage
pur Python du protocole BLOCKS de ROLI (BLOCKS-SDK, C++/JUCE).

- Licence d'origine : ISC (même licence que le BLOCKS-SDK de ROLI), d'après
  le README du projet.
- Copié le 15 novembre 2026 (dépôt à cette date, firmware testé par
  l'auteur : Lightpad Block M 1.1.0, macOS).

## Modifications apportées ici

- `transport.py` : appariement des ports MIDI entrée/sortie en ignorant
  l'index numérique que python-rtmidi ajoute au nom du port (indispensable
  sous Windows, où cet index diffère entre la liste des entrées et celle des
  sorties).
- Paquet renommé `roli_blocks` pour éviter toute confusion avec un éventuel
  paquet `roli` installé par ailleurs.

Le bytecode LittleFoot embarqué dans `_bytecode.py` est la compilation du
programme `BitmapLEDProgram` du BLOCKS-SDK (grille 15 × 15, RGB565).
