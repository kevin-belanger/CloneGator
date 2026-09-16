# CloneGator — Plan de développement

Compagnon de [ANALYSE-FONCTIONNELLE.md](ANALYSE-FONCTIONNELLE.md), révision 0.3.
Les renvois `§n` pointent vers l'analyse.

| Rév. | Date | Auteur | Changement |
|------|------|--------|------------|
| 0.1 | 2026-09-16 | Kevin + Claude | Découpage initial en modules et en phases |
| 0.2 | 2026-09-16 | Kevin + Claude | Deux stations disponibles pour le développement : essais sur matériel réel en continu, l'ancienne phase 5 est dissoute. Ajout du critère « la cible démarre » |
| 0.3 | 2026-09-16 | Kevin | Pas de garde-fou de développement supplémentaire. P2 suffit : le système et Claude Code vivent sur USB, et le code exclut les USB de toute écriture. On code d'abord, on traitera les problèmes quand ils se présenteront |

---

## 1. Les deux idées qui structurent tout le plan

### 1.1 Une seule couche touche le système

Tout appel à une commande externe et toute lecture de `/dev`, `/sys` ou `/proc` passe par un
module unique. Rien d'autre dans le projet n'appelle `subprocess`.

Ce n'est pas de la propreté gratuite, ça achète trois choses :

- **un délai d'attente sur chaque commande** — `clonesrv` pouvait se figer indéfiniment sur un
  disque bloqué, sans rien afficher ;
- **le journal complet des commandes réellement lancées**, verbatim, dans le journal d'opération ;
- **le point d'échange** qui permet de faire tourner tout le reste du logiciel sur un faux
  matériel.

### 1.2 Deux niveaux d'essai, pas un seul

Le développement se fait **sur une station réelle**, disques dans les baies. Ça ne supprime pas
le banc virtuel : ça lui donne son vrai rôle, qui est d'être le niveau rapide.

**Niveau rapide — le banc en boucle.** Des fichiers creux montés en `loop`, réellement
partitionnés, avec de vrais systèmes de fichiers NTFS, ext4 et vfat contenant de vrais fichiers.
Mêmes commandes, mêmes formats, mêmes vérifications que sur du matériel. Un cycle complet prend
quelques secondes contre une dizaine de minutes au minimum sur un vrai 500 Go. C'est ce niveau
qu'on utilise pour corriger un bogue : on ne met pas au point du code à dix minutes par essai.

Il apporte aussi ce que le matériel donne difficilement — des situations fabriquées à la demande
et identiques à chaque exécution :

- une cible plus petite que la source
- une cible portant une table de partitions périmée
- une source dont les numéros de partition ne sont pas contigus (1, 2, 3, **5**)
- une GPT volontairement abîmée, une partition sans système de fichiers reconnu
- une partition montée au moment du lancement

Ce sont exactement les situations où `clonesrv` échouait mal. Un banc qui ne les contient pas ne
prouve rien.

**Niveau de validation — les vraies baies.** Débits réels, correspondance des ports avec la
sérigraphie, SMART sur des disques usagés, retrait à chaud, et surtout le seul critère qui
compte vraiment pour l'école : **un disque cloné démarre-t-il ?** Aucune simulation ne répond à
cette question. C'est le gain principal d'avoir le matériel sous la main dès le premier jour, et
c'est pourquoi l'ancienne phase « ce qui exige la station » n'existe plus comme phase séparée :
ses vérifications sont réparties dans chaque phase, au moment où elles ont un sens.

**Le banc simulé** — un fichier JSON décrivant un parc de disques fictifs — garde une utilité
plus étroite : produire à volonté un état qu'on ne peut pas provoquer sur commande, comme « le
port 4 contient un disque que SMART déclare défaillant », pour mettre au point les écrans. Il
peut attendre la phase de l'interface.

---

## 1.3 Environnement de développement

**Deux machines, deux rôles.**

- **Station A — développement.** Claude Code y tourne, les disques de test y sont branchés,
  elle est destinée à être malmenée. Le dépôt git y vit, et les deux documents d'analyse et de
  plan y sont versionnés avec le code : une spec vivante appartient au dépôt, pas à un dossier
  à part.
- **Station B — recette.** On n'y développe pas. Elle sert à vérifier que le paquet `.deb`
  s'installe sur une machine propre et que la station fonctionne sans rien d'autre que lui.
  C'est elle qui valide la phase 5.

**Pas de garde-fou de développement supplémentaire.** Décision de Kevin, révision 0.3. Le
système de la station et Claude Code vivent sur un disque USB, et P2 exclut tout disque USB de
la moindre écriture : la machine de travail est protégée par la règle même qu'on écrit. Les
disques présents dans les baies sont des disques d'essai, sacrifiés par définition.

Une couche de sécurité de plus alourdirait un projet qui démarre sans rien protéger de réel. On
code, et si un problème se présente, on le traite à ce moment-là.

**Conséquence sur le code :** P2 n'est pas une vérification parmi d'autres, c'est la règle
porteuse. Elle vit à un seul endroit — l'attribution du rôle d'un disque dans `devices.py` — et
un disque dont le bus est `usb` n'y obtient jamais le rôle source ni cible. Tout le reste en
découle.

---

## 2. Modules

```
clonegator/
  __main__.py        point d'entrée, sous-commandes de développement
  sysexec.py         exécution des commandes externes : délai, capture, journal
  devices.py         inventaire des disques — deux fournisseurs, réel et simulé
  layout.py          tables de partitions : lecture, numéros réels, reproduction
  filesystems.py     détection du contenu et choix du moteur par partition (§6.2)
  storage.py         disques USB de stockage : candidats, espace libre, refus FAT32
  health.py          état SMART (§12)
  engine/
    fanout.py        une lecture → N écritures indépendantes, verdict par cible
    clone.py         disque → disques (§6)
    backup.py        disque → image (§7)
    restore.py       image → disques (§8)
  image.py           format d'image : métadonnées, nommage, empreintes, marque « incomplet »
  verify.py          vérification légère (§11)
  journal.py         journal d'opération (§10)
  ui/
    model.py         état affiché, sans curses — testable seul
    screens.py       rendu curses (§9)
  config.py
```

Deux séparations méritent qu'on y tienne :

- **`ui/model` ne connaît pas curses.** L'état affiché est une structure de données ordinaire.
  On peut le tester et l'imprimer en texte brut sans jamais ouvrir un écran.
- **`engine/fanout` ne connaît ni les disques ni les partitions.** Il diffuse un flux vers N
  destinations et rapporte un verdict par destination. Il se teste avec des fichiers.

---

## 3. Phases

Principe d'ordonnancement : **ce qui est risqué passe en premier**, pas en dernier. Les deux
choses qui ont fait mal dans `clonesrv` sont la gestion d'échec par cible et la fragilité de la
détection des ports. Avec le matériel disponible dès le départ, les deux se traitent tôt : la
première en phase 1, la seconde dès la phase 0.

Chaque phase se met au point sur le banc en boucle et se **valide sur les vraies baies**.

### Phase 0 — Socle, banc et garde-fous · taille M

- dépôt git sur la station A, les deux documents versionnés avec le code
- squelette du paquet, `CLONEGATOR_VERSION` affichée et journalisée dès le premier jour
- `sysexec` avec délai d'attente et journalisation
- `devices` : le modèle de données d'un disque, la détection réelle, et **l'attribution des
  rôles — où vit P2**
- le banc en boucle, cas d'échec compris (§1.2)
- `journal` minimal
- **correspondance des ports `by-path` avec la sérigraphie**, vérifiée baie par baie, tolérante
  aux deux formes de nommage du noyau (`ata-1` et `ata-1.0`) — le piège qui attend `clonesrv` à
  la prochaine mise à jour, réglé d'entrée puisque le matériel est là

**Fini quand** : `clonegator inventaire` affiche le tableau des ports, et un disque déplacé de
baie en baie apparaît chaque fois au bon numéro.

### Phase 1 — Le moteur de diffusion · taille L

C'est le cœur du projet et la partie que bash faisait mal. Elle se développe isolément.

- une source lue **une seule fois**, N consommateurs indépendants
- un tampon dimensionné par cible, pour que les à-coups d'un disque ne se propagent pas aux
  autres
- une cible morte est retirée, les autres continuent (§6.4)
- une cible dont le tampon est plein et qui n'a rien écrit depuis un délai donné est déclarée
  bloquée, abandonnée, signalée (§6.4)
- un verdict par cible, jamais un verdict global

**Point à ne pas se raconter d'histoires :** le tampon absorbe la gigue, pas une lenteur durable.
Une cible réellement plus lente finira par brider la lecture — c'est physique, et c'est
exactement ce que demande le §6.4. Ce qu'on élimine, c'est le blocage en tête de file d'un
`tee` avec 64 Ko de tampon.

**Fini quand** : la diffusion d'un fichier de plusieurs Go vers cinq destinations donne cinq
empreintes identiques à la source ; en tuant une destination en cours de route, les quatre
autres restent correctes et la cinquième est rapportée en échec ; et sur les vraies baies, le
débit vers cinq disques tient celui du plus lent — mesuré, pas supposé.

### Phase 2 — Tables de partitions et clonage · taille L

- `layout` : lecture de la table (`sfdisk --json` donne directement du JSON exploitable),
  **énumération des numéros réels** de partition, reproduction sur la cible, repositionnement de
  l'en-tête GPT de secours
- `filesystems` : le tableau du §6.2
- `engine/clone` : assemblage des phases 1 et 2, plus la validation préalable du §6.5
- `verify` : la vérification légère du §11

S'ajoute ici, le matériel étant disponible : `blockdev --setro` sur le port 1 (P1), vérifié en
essayant délibérément d'écrire dessus, et la validation du §6.5 sur de vrais disques.

**Fini quand** : un clonage boucle → trois boucles reproduit l'arborescence à l'identique sur
les trois, y compris avec une source aux numéros non contigus ; une cible trop petite est
écartée **avant** que la moindre écriture ait lieu sur les autres ; et surtout — **un vrai
disque Windows cloné vers deux vraies cibles démarre sur une machine**. C'est le critère
d'acceptation réel du projet, et c'est la première fois qu'on peut le vérifier.

### Phase 3 — Images · taille M

- `image` : le format du §7.2, les deux modes, la marque « incomplet »
- `storage` : candidats USB, espace libre, refus explicite de FAT32
- `engine/backup` et `engine/restore`

**Fini quand** : l'aller-retour disque → image → disque redonne une arborescence identique,
dans les deux modes ; une image tronquée à la main est refusée par la vérification des
empreintes **avant** toute écriture sur les cibles ; un dossier marqué incomplet n'apparaît pas
dans la liste ; et un vrai disque restauré depuis une image démarre.

### Phase 4 — Interface · taille M

- `ui/model` d'abord, testable et imprimable en texte brut
- `ui/screens` ensuite : les quatre écrans du §9
- l'écran de rapport qui ne s'efface jamais tout seul (§9.4)
- le banc simulé, ici seulement, pour produire à volonté les états qu'on ne peut pas provoquer :
  disque défaillant au SMART, cible trop petite, six baies pleines
- rafraîchissement du tableau à l'insertion et au retrait d'un disque — testé en débranchant
- `health` : SMART sur de vrais disques usagés
- comportement au retrait à chaud d'une cible en cours de copie (§13)

**Fini quand** : le parcours complet — inventaire, confirmation, progression, rapport — tourne
sur la station A, et le retrait volontaire d'une cible en cours de copie est signalé à l'écran
sans perturber les autres.

### Phase 5 — Paquet et recette · taille S · **sur la station B**

- `.deb`, unité systemd, `/etc`, `/var/log`
- un README qui tient en un écran

**Fini quand** : la station B, qui n'a jamais servi au développement, s'installe depuis le
paquet seul, démarre sur l'interface, et clone un disque qui démarre.

---

## 4. Chemin critique

```
Phase 0 ──┬── Phase 1 ── Phase 2 ── Phase 3 ──┐
          │                                   ├── Phase 5
          └── Phase 4 ───────────────────────┘
```

La phase 4 (interface) ne dépend que de la phase 0 : elle peut avancer en parallèle du moteur.
C'est la seule parallélisation réellement utile si le projet se fait à plusieurs.

Le jalon qui compte n'est pas la fin d'une phase mais la **fin de la phase 2** : à ce moment,
un disque cloné démarre. Tout ce qui suit ajoute des usages autour d'un moteur déjà prouvé.

---

## 5. Règles de travail héritées de `clonesrv`

- **Une seule copie du code, versionnée.** Pas de `v1`, `v2`, `V3`, `V4` côte à côte. L'historique
  vit dans git.
- **Ce qui s'exécute est ce qui est installé.** Le numéro de version s'affiche dans le menu et
  s'écrit dans chaque journal, pour qu'on ne se demande jamais quelle version a tourné.
- **Pas de code de débogage laissé en place.** Le journal est la seule sortie de diagnostic, et
  il a un emplacement et une durée de vie définis.
- **Aucune dépendance Python tierce** (§16). Si un besoin semble en réclamer une, c'est une
  discussion, pas une décision de passage.

---

## 6. Premier pas concret

Sur la station A, dans l'ordre :

1. Debian à jour, `git`, `python3`, `partclone`, `gdisk`, `zstd`, `smartmontools`, et Claude Code
   — le tout sur le disque USB système.
2. Le dépôt, avec les deux documents dedans.
3. Le script du banc en boucle, cas d'échec compris.
4. `clonegator inventaire`, qui affiche le tableau des ports.
5. Relever la correspondance entre les baies et les chemins `by-path`, en déplaçant un même
   disque de baie en baie.

Le point 3 passe avant le reste : sans lui, les essais sont trop lents pour qu'on puisse
réellement itérer.
