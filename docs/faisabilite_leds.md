# Pilotage des LEDs du Lightpad BLOCK depuis Python — étude de faisabilité

*Point 9 du cahier des charges — 15 novembre 2026*

## Verdict

**Faisable, en pur Python, sans utilitaire C++ et sans ROLI Dashboard.**

Le Lightpad se pilote par des messages SysEx propriétaires (protocole BLOCKS),
envoyés sur le même port MIDI USB que les notes. Un portage Python complet de ce
protocole existe (projet open source *roli-blocks-py*, licence ISC) ; il ne dépend
que de `python-rtmidi`, déjà installé sur ce PC. Je l'ai intégré au projet dans le
dossier `roli_blocks/`, adapté pour Windows, et ses 31 tests unitaires passent ici.

Un prototype `prototypes/proto_zone_rouge.py` allume une zone en rouge.
**Validé sur le pad le 15 novembre 2026** : Lightpad Block M (série
`LPM81GIDU163I3CQ`), firmware 1.1.0, Windows 11. Topologie reçue, mode API actif,
zone 4 × 4 rouge affichée, touchers reçus avec la zone calculée, retour à
l'affichage habituel après le script.

**Retour visuel à l'appui validé le même jour** (option `--feedback`) : une
trentaine d'appuis sur 12 zones, éclaircissement jugé fluide par l'utilisateur, sans
retard ni zone restée allumée. Méthode retenue : à chaque début/fin d'appui, renvoi
de la grille complète (trame unique d'environ 590 octets) au lieu d'un
différentiel. La perte de paquets pendant un toucher décrite plus bas n'a donc pas
eu d'effet visible avec ce firmware ; la variante B (programme LittleFoot sur
mesure) devient optionnelle.

## Comment ça marche

### 1. Le protocole BLOCKS en deux mots

| Élément | Détail |
|---|---|
| Enveloppe | `F0 00 21 10 77 [index périphérique] [charge utile] [checksum] F7` |
| Charge utile | flux de bits « 7 bits par octet », poids faible en premier |
| Checksum | 1 octet calculé sur la charge utile seule |
| Message hôte → pad | `MessageType (7 bits)` + corps spécifique |

Messages utiles pour nous :

- `deviceCommand` : `requestTopology`, `beginAPIMode`, `endAPIMode`, `ping`,
  `saveProgramAsDefault` ;
- `sharedDataChange` : écriture d'octets dans la mémoire partagée du pad (c'est
  par là que passent **et** le programme **et** les pixels) ;
- `programEventMessage` : 3 entiers 32 bits envoyés au programme qui tourne sur le
  pad (callback `handleMessage`).

Le pad répond par des paquets `touchStart/Move/End` (position 12 bits, pression
8 bits, vitesse), `controlButtonDown/Up`, `packetACK`, `deviceTopology`…

### 2. Les LEDs ne se pilotent pas directement

Il n'existe **aucun message « allume le pixel (x, y) »**. Le pad exécute un petit
programme écrit en *LittleFoot* (langage de ROLI, compilé en bytecode). C'est ce
programme qui dessine les 15 × 15 LEDs, 25 fois par seconde, dans sa fonction
`repaint()`. L'hôte influence l'affichage en écrivant dans la mémoire partagée
(le « heap ») que le programme lit.

Le SDK fournit un programme générique, `BitmapLEDProgram` (107 octets de
bytecode) : il lit dans le heap une image 15 × 15 en RGB565 (2 octets par LED,
450 octets) et l'affiche. Le bytecode compilé est embarqué dans
`roli_blocks/_bytecode.py`, donc **aucun compilateur n'est nécessaire** côté
application.

### 3. Séquence côté Python (ce que fait le prototype)

1. Repérer la paire de ports MIDI (entrée + sortie) dont le nom contient
   « BLOCK ».
2. `requestTopology` → le pad renvoie son numéro de série, firmware, batterie.
3. `endAPIMode` puis `beginAPIMode` → le pad bascule en « mode API ».
4. Thread de **ping toutes les 400 ms** (sans ping pendant 5 s, le pad quitte le
   mode API et affiche une animation de recherche).
5. `sharedDataChange` : mise à zéro des 7 200 octets du heap, puis écriture des
   107 octets du programme bitmap à l'offset 0. Le pad vérifie la somme de
   contrôle et démarre le programme.
6. Pour afficher : on écrit les pixels RGB565 à partir de l'offset 107, soit en
   différentiel (seuls les octets changés), soit en trame complète (450 octets,
   ≈ 590 octets de SysEx).
7. À la fermeture : `endAPIMode` → le pad doit revenir à son programme habituel.

Une zone 4 × 4 en rouge tient dans **une trame SysEx de 58 octets**
(vérifié par le dry-run du prototype).

## Conséquence importante pour l'architecture de l'application

En mode API, **le programme d'usine du pad (grille de notes « Note Grid ») est
remplacé** par le nôtre tant que l'application tourne. Deux effets :

1. **Le pad n'émet plus de notes MIDI de lui-même.** L'application reçoit à la
   place les touchers bruts (x, y en 0..4095, pression, vitesse) via le
   protocole BLOCKS et doit calculer elle-même la zone touchée. C'est en fait un
   avantage : la grille de zones devient entièrement libre (nombre, taille,
   interstices), indépendante de ce que Dashboard a configuré.
2. Le script actuel `Test_ROLI.py` (lecture des `note_on` avec mido) ne verra plus
   rien pendant que l'application pilote les LEDs. Si l'application doit aussi
   **envoyer** des notes MIDI à un DAW ou à un synthé, elle le fera elle-même
   (port MIDI virtuel ou port de sortie), ce qui est trivial avec rtmidi.

Si l'on tient absolument à garder les notes MIDI émises par le pad, il faut passer
par un programme LittleFoot sur mesure (voir variante B ci-dessous) qui appelle
`sendNoteOn` lui-même.

## Deux variantes pour l'intégration

### Variante A — bitmap piloté par l'hôte (celle du prototype)

- Python écrit les 15 × 15 pixels ; `BitmapLEDProgram` les affiche.
- **Avantages** : zéro compilation, déjà fonctionnel, couleurs quelconques,
  changement de profil/page = on renvoie une trame.
- **Limite connue** (observée par l'auteur de roli-blocks-py, firmware 1.1.0) :
  pendant qu'un doigt est posé, le pad est occupé à émettre les touchers et
  **perd une partie des `sharedDataChange` entrants**. Le retour visuel à
  l'appui peut donc arriver avec un retard ou être perdu. Contournements : envoi
  en trame complète avec un rafraîchissement périodique (100 ms) tant qu'un
  toucher est actif (dès qu'une trame passe, l'affichage est resynchronisé), ou
  retour visuel appliqué au relâchement. Le vrai correctif du SDK (suivi des
  `packetACK` + réémission à 250 ms) est implémentable en Python si besoin.

### Variante B — programme LittleFoot « zones » sur mesure (recommandée à terme)

- Le programme tournant sur le pad connaît la grille de zones : il lit dans le
  heap une table « zone → couleur » (16 zones × 3 octets = 48 octets au lieu de
  450) et dessine les blocs lui-même.
- Le **retour visuel à l'appui est calculé sur le pad** (callbacks `touchStart` /
  `touchEnd` de LittleFoot) : latence nulle, et le problème de perte de paquets
  disparaît puisque l'hôte n'a rien à envoyer au moment du toucher.
- Le programme peut aussi émettre les notes MIDI (`sendNoteOn`) et/ou remonter
  la zone touchée à l'hôte (`sendMessageToHost`), au choix.
- **Coût** : il faut compiler ce programme **une fois** pour obtenir son bytecode
  (ensuite embarqué dans le projet comme l'est `BitmapLEDProgram`). Le
  compilateur LittleFoot est un en-tête C++ de 2 450 lignes du module
  `roli_blocks_basics`, qui ne dépend que de `juce_core`. Sur ce PC : `g++`
  (MSYS2) est disponible, pas de compilateur Microsoft ; compiler `juce_core` avec
  MinGW est possible mais c'est l'étape à risque. Alternative sans compilation :
  BLOCKS Code / Dashboard de ROLI compilent les `.littlefoot`, mais ne sont pas
  installés ici et dépendent de la disponibilité des outils ROLI.
- Le runtime reste 100 % Python : on envoie le bytecode compilé exactement comme
  le programme bitmap aujourd'hui.

**Décision après les tests du 15 novembre 2026** : la variante A couvre les trois
besoins du point 9 (couleurs par zone, retour visuel à l'appui, changement avec le
profil/la page) avec un retour visuel jugé fluide. On intègre A, en renvoyant la
grille complète à chaque changement. La variante B reste une option si un jour la
latence ou la fiabilité se dégradent, ou si l'on veut que le pad émette lui-même
les notes MIDI.

## Pourquoi pas l'utilitaire C++ (roli_blocks_basics + JUCE) ?

C'était le plan de repli demandé. Il n'est pas nécessaire : tout ce que le module
JUCE fait sur le fil est reproduit par la couche Python (c'est précisément le
travail du projet roli-blocks-py, validé sur un Lightpad réel). Un utilitaire C++
imposerait de construire JUCE sous Windows (le module exige `juce_audio_devices`,
donc une application JUCE complète), sans compilateur Microsoft sur ce poste, et
d'ajouter un processus externe à piloter. Seul le compilateur LittleFoot (variante
B) justifie éventuellement un passage ponctuel par du C++, hors application.

## Risques et inconnues à lever au test physique

| Point | Pourquoi | Comment on le verra |
|---|---|---|
| Version du firmware | Le portage Python a été vérifié sur un Lightpad Block **M**, firmware **1.1.0**. Un Lightpad première génération (`LPB…`) ou un firmware différent peut se comporter autrement. | Le prototype affiche série et firmware au démarrage. |
| SysEx sous Windows | roli-blocks-py n'a été testé que sous macOS. Windows (WinMM) gère bien les SysEx longs, mais c'est à confirmer, en particulier la réception de la topologie. | La demande de topologie doit recevoir une réponse en moins de 2,5 s. |
| Appariement des ports | Windows suffixe les noms de ports d'un index différent en entrée et en sortie ; j'ai corrigé l'appariement en conséquence. | `--list-ports` doit afficher une paire BLOCKS. |
| Retour au programme d'usine | Attendu après `endAPIMode`, à confirmer visuellement. | Le pad réaffiche sa grille habituelle après le script. |
| Pad en veille | Le pad s'endort seul ; la topologie ne répond alors pas. | Le prototype réessaie 3 fois ; toucher le pad le réveille. |
| Dashboard | S'il est lancé, il verrouille le pad (pas installé sur ce PC). | Aucun port ou aucune réponse → le fermer. |

## Protocole de validation proposé

1. Brancher le pad en USB, fermer tout logiciel ROLI.
2. `python prototypes/proto_zone_rouge.py --list-ports` → une paire BLOCKS.
3. `python prototypes/proto_zone_rouge.py` → la zone en haut à gauche (LEDs 0..3 ×
   0..3) s'allume en rouge pendant 8 s, les touchers défilent dans la console
   avec la zone calculée, puis le pad s'éteint et revient à son affichage normal.
4. `python prototypes/proto_zone_rouge.py --feedback --zone 2 1 --color 0080ff`
   → zone bleue colonne 2 ligne 1 ; les zones touchées s'éclaircissent pendant
   l'appui (c'est le test de la limite « pertes pendant un toucher »).
5. Variante de grille : `--cols 5 --rows 5` (zones 3 × 3) ou `--gap 1`
   (4 zones de 3 LEDs séparées d'une LED).

## Si le pilotage échouait quand même

Si le pad ne répond pas au protocole (firmware incompatible, SysEx bloqués), on
garde les couleurs dans l'interface seulement, comme prévu au cahier des charges.
Il n'y a pas de voie intermédiaire : sans mode API, le pad affiche uniquement ce
que son programme d'usine décide.

## Sources

- Module de référence ROLI : https://github.com/WeAreROLI/roli_blocks_basics
  (`protocol/roli_BlocksProtocolDefinitions.h`, `protocol/Protocol Format.txt`,
  `littlefoot/roli_LittleFootCompiler.h`, `roli_LittleFootFunctions.dox`).
- Portage Python du protocole : https://github.com/kairess/roli-blocks-py
  (code repris dans `roli_blocks/`, notes de protocole dans
  `docs/PROTOCOL_blocks_notes_kr.md`, en coréen, chapitre 11 = comportements
  observés sur pad réel).
- Autre implémentation Python indépendante (Linux/macOS, Python 3.13) confirmant
  l'approche : https://github.com/hyperb1iss/blocksd
- Interface C/Lua du SDK, chargement de LittleFoot sans Dashboard :
  https://github.com/agraef/myblocks
