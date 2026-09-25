# CloneGator — Plan de développement

Compagnon de [ANALYSE-FONCTIONNELLE.md](ANALYSE-FONCTIONNELLE.md), révision 1.1.
Les renvois `§n` pointent vers l'analyse.

| Rév. | Date | Auteur | Changement |
|------|------|--------|------------|
| 0.1 | 2026-09-16 | Kevin + Claude | Découpage initial en modules et en phases |
| 0.2 | 2026-09-16 | Kevin + Claude | Deux stations disponibles pour le développement : essais sur matériel réel en continu, l'ancienne phase 5 est dissoute. Ajout du critère « la cible démarre » |
| 0.3 | 2026-09-16 | Kevin | Pas de garde-fou de développement supplémentaire. P2 suffit : le système et Claude Code vivent sur USB, et le code exclut les USB de toute écriture. On code d'abord, on traitera les problèmes quand ils se présenteront |
| 0.4 | 2026-09-24 | Kevin + Claude | Phase 1 terminée et mesurée sur les baies. Le banc en boucle n'est plus le « niveau rapide » : les SSD des baies sont assez rapides pour itérer. Il devient une fabrique de sources et de cibles impossibles à obtenir sur les baies sans sacrifier le maître. Pannes de cibles simulées par `/sys` sur les vrais disques |
| 0.5 | 2026-09-24 | Kevin + Claude | Phase 2 terminée : un Windows cloné vers cinq cibles démarre sur cinq machines différentes. Résultats et enseignements consignés |
| 0.6 | 2026-09-24 | Kevin + Claude | Analyse 0.4 : mode libre par défaut et mode station en raccourci, emplacements, P1 et P2 reformulés, format d'image arrêté. La phase 3 fait de la restauration un clonage dont la source est une image ; la phase 4 porte les emplacements, les modes et les filets de P2. Note de transition sur les rôles du code actuel |
| 0.7 | 2026-09-25 | Kevin + Claude | Phase 3 terminée : une image du Windows du port 1, restaurée vers trois cibles effacées, démarre sur trois machines. Montage d'un disque USB dédié et essai du refus FAT32 reportés en phase 4, faute de disque |
| 0.8 | 2026-09-25 | Kevin + Claude | Analyse 0.5 : interface arrêtée, partage réseau Windows dans le MVP. La phase 4 porte l'interface du §9, les modes, le partage réseau et le lancement automatique du mode station |
| 0.9 | 2026-09-25 | Kevin + Claude | Phase 4 terminée sur la station A, essais avec un disque USB compris (montage, FAT32, cible). Restent un redémarrage réel en lancement automatique et un disque réellement usé pour SMART. Enseignements : réserve d'écriture commune, retrait à chaud, partage réseau lent, unité systemd |
| 1.0 | 2026-09-25 | Kevin + Claude | Phase 5 terminée : paquet .deb publié en release GitHub, installé et éprouvé sur une machine neuve. MVP livré |
| 1.1 | 2026-09-25 | Kevin + Claude | Analyse 0.8 : phase 6, le CloneGator live, première étape vers le mode PXE (§17) |

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

### 1.2 Essayer sur les baies, fabriquer ce qu'elles ne donnent pas

Le développement se fait **sur une station réelle**, disques dans les baies. C'est le niveau
d'essai principal, et pas seulement celui de la validation finale.

L'idée de départ était de mettre au point sur un banc en boucle, « à quelques secondes par cycle
contre une dizaine de minutes sur un vrai 500 Go ». La phase 1 l'a démentie : les cibles sont des
SSD, un disque source réel ne contient qu'une fraction de sa capacité, et la copie par blocs
utilisés rend un cycle sur les baies assez court pour itérer. Le moteur de diffusion a, lui, été
mis au point sans aucun disque, avec des tubes, puis mesuré directement sur les baies.

**Les baies apportent** ce qu'aucune simulation ne donne : débits réels, correspondance des ports
avec la sérigraphie, SMART sur des disques usagés, retrait à chaud, et surtout le seul critère qui
compte vraiment pour l'école : **un disque cloné démarre-t-il ?**

Elles permettent aussi de provoquer des pannes de cible sur de vrais disques, sans rien débrancher :

- `echo offline > /sys/block/sdX/device/state` : la cible cesse de répondre, ses écritures
  échouent ;
- `echo 1 > /sys/block/sdX/device/delete` : la cible disparaît, comme retirée à chaud ; un
  rescan de l'hôte SCSI la fait revenir.

**Le banc en boucle devient une fabrique de cas.** Il reste utile pour ce que les baies ne peuvent
pas donner sans sacrifier le maître du port 1 ou acheter des disques :

- une source dont les numéros de partition ne sont pas contigus (1, 2, 3, **5**)
- une source à GPT volontairement abîmée, à partition sans système de fichiers reconnu, à
  système de fichiers marqué sale
- une cible plus petite que la source — les cinq disques des baies font tous 480 Go

Ces disques en boucle n'ont pas de port et n'obtiennent donc aucun rôle dans `devices` : ils
servent aux essais de `engine/clone` et suivants, qui reçoivent des chemins et ne connaissent pas
les ports. Une source du banc peut être clonée vers des cibles des baies.

Ce sont exactement les situations où `clonesrv` échouait mal. Des essais qui ne les contiennent
pas ne prouvent rien.

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
système de la station et Claude Code vivent sur un disque USB utilisé par le système : le premier
filet de P2 le protège, et le noyau lui-même refuse de l'ouvrir en exclusivité. La machine de
travail est protégée par la règle même qu'on écrit. Les disques présents dans les baies sont des
disques d'essai, sacrifiés par définition.

Une couche de sécurité de plus alourdirait un projet qui démarre sans rien protéger de réel. On
code, et si un problème se présente, on le traite à ce moment-là.

**Conséquence sur le code :** les règles qui décident ce qu'un disque peut devenir — emplacement,
mode, filets de P2 — vivent à un seul endroit, `devices.py`. Le moteur ne regarde jamais le rôle
d'un disque : il copie ce qu'on lui donne. Tout le reste en découle.

---

## 2. Modules

```
clonegator/
  __main__.py        point d'entrée, sous-commandes de développement
  sysexec.py         exécution des commandes externes : délai, capture, journal
  devices.py         inventaire, emplacements (§3.1), filets de P2 — le seul endroit où se
                     décide ce qu'un disque peut devenir
  layout.py          tables de partitions : lecture, numéros réels, reproduction
  filesystems.py     détection du contenu et choix du moteur par partition (§6.2)
  storage.py         disques USB de stockage : candidats, espace libre, refus FAT32
  health.py          état SMART (§12)
  engine/
    fanout.py        une lecture → N écritures indépendantes, verdict par cible
    clone.py         une source → N disques (§6) ; la source d'une partition est un disque
                     ou un fichier d'image, et la restauration (§8) n'est que ce second cas
    backup.py        disque → image (§7)
  image.py           format d'image (§7.2) : métadonnées, LISEZMOI, empreintes, complétude
  verify.py          vérification légère (§11)
  journal.py         journal d'opération (§10)
  ui/
    model.py         état affiché, sans curses — testable seul
    screens.py       rendu curses (§9)
  reseau.py          partage réseau Windows : montage, identifiants jamais écrits
  config.py          réglages dans /etc/clonegator/ : mode, réglage de station, lancement
                     automatique, connexion réseau sans mot de passe
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

Chaque phase se met au point et se valide **sur les vraies baies**, complétées par le banc pour les cas qu'elles ne donnent pas (§1.2).

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

### Phase 1 — Le moteur de diffusion · taille L · **terminée le 2026-09-24**

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

**Résultat.** `engine/fanout.py`, douze essais sans disque (`python3 -m unittest tests.test_fanout`)
et la sous-commande `essai-diffusion` pour les baies. Mesures sur les cinq SSD Kingston SA400 :

| Essai | Débit |
|-------|-------|
| 4 Gio vers cinq tubes, une destination tuée à mi-course | 4 empreintes identiques, la 5ᵉ en échec |
| cinq cibles, 32 Gio, source en mémoire | 232 Mo/s |
| la cible la plus lente seule (port 5), 32 Gio | 241 Mo/s |
| port 1 → cinq cibles, 16 Gio, relecture depuis le disque | 5 sur 5 identiques, 200 Mo/s |

Ce que la mesure a appris, et qu'on n'aurait pas deviné :

- **Sur un tube, `read` rend 64 Kio**, quelle que soit la taille demandée. Le moteur remplit
  chaque bloc et agrandit les tubes à 1 Mio : la source `partclone` de la phase 2 est un tube.
- **Un `fsync` périodique coûte 30 %** : chacun fige la cible le temps de vider son cache. Un
  seul `fsync` en fin de flux, qui remonte toute erreur d'écriture différée.
- **Les disques d'un même modèle ne se valent pas** : le port 5 écrit à 241 Mo/s sur la durée,
  ses voisins à près de 400. Le groupe suit le plus lent, comme prévu.
- **Le maître actuel se lit mal** : 40 à 300 Mo/s dans sa zone de données, 525 Mo/s au-delà.
  Un SSD bas de gamme dont les données ont vieilli. C'est lui qui fixe le rythme d'un clonage réel.
- **`blockdev --setro` n'est pas vérifié à l'ouverture**, seulement à l'écriture (EPERM). Un
  essai de P1 doit donc écrire, pas seulement ouvrir — en réécrivant à l'identique pour ne rien
  risquer.

### Phase 2 — Tables de partitions et clonage · taille L · **terminée le 2026-09-24**

- `layout` : lecture de la table (`sfdisk --json` donne directement du JSON exploitable),
  **énumération des numéros réels** de partition, reproduction sur la cible, repositionnement de
  l'en-tête GPT de secours
- `filesystems` : le tableau du §6.2
- `engine/clone` : assemblage des phases 1 et 2, plus la validation préalable du §6.5
- `verify` : la vérification légère du §11

S'ajoute ici, le matériel étant disponible : `blockdev --setro` sur le port 1 (P1), vérifié en
essayant délibérément d'écrire dessus, et la validation du §6.5 sur de vrais disques.

**Fini quand** : un clonage vers trois cibles des baies reproduit l'arborescence à l'identique
sur les trois, y compris depuis une source du banc aux numéros non contigus ; une cible trop petite est
écartée **avant** que la moindre écriture ait lieu sur les autres ; et surtout — **un vrai
disque Windows cloné vers deux vraies cibles démarre sur une machine**. C'est le critère
d'acceptation réel du projet, et c'est la première fois qu'on peut le vérifier.

**Résultat.** `layout`, `filesystems`, `verify`, `journal`, `engine/clone` et la sous-commande
`cloner`. Essais automatiques du banc : `python3 -m unittest tests.test_clone_banc`.

| Essai | Résultat |
|-------|----------|
| Windows 11 du port 1 (GPT, 5 partitions, 23 Go utilisés) → cinq cibles | 5 réussies en 2,2 min |
| les cinq cibles démarrées sur cinq machines différentes (Kevin) | **démarrage normal, aucun message** |
| contre-vérification du port 2 : partition EFI, fichiers de C: | identiques, 268 621 fichiers des deux côtés |
| source du banc numérotée 1-2-3-5 → trois cibles des baies | 1-2-3-5 sur les trois, GPT sans défaut |
| source GPT du banc → cible trop petite parmi trois | écartée avant toute écriture, les autres conformes au manifeste |
| cible du banc portant une ancienne table MBR et un ext4 | aucune trace de l'ancien contenu |
| cible des baies dont une partition est montée | écartée, sa table intacte |

Ce que les essais ont appris :

- **partclone ne copie pas le secteur d'amorçage de secours de NTFS**, logé juste après la fin
  du volume, hors des clusters. La vérification légère l'a trouvé au premier essai : `ntfsfix`
  déclarait la cible incohérente. Le moteur recopie ce secteur, à la position que donne le
  secteur d'amorçage lui-même.
- **Les GUID sont reproduits à l'identique**, disque et partitions : le chargeur de Windows
  désigne ses partitions par eux. Deux clones ne doivent donc pas être branchés dans la même
  machine, comme deux copies de n'importe quel disque.
- **Une partition sans système de fichiers n'est pas une anomalie** : la partition réservée
  de Windows n'en a jamais. Elle est copiée en brut sans avertissement ; l'avertissement du
  §6.2 est réservé aux systèmes de fichiers sales.
- **Les noms `/dev/sdX` changent** quand on retire et remet les disques : après le test de
  démarrage, le port 6 était passé de `sdb` à `sdg`. Le logiciel ne raisonne qu'en ports (P3).

### Phase 3 — Images · taille M · **terminée le 2026-09-25**

- `image` : le format du §7.2 — `clonegator.json` écrit en dernier, `LISEZMOI.txt`, empreintes
  au format `sha256sum`, les deux modes
- `storage` : candidats USB, espace libre, refus explicite de FAT32
- `engine/clone` : la source de chaque partition devient interchangeable, disque ou fichier
  d'image. La restauration réutilise ainsi la mécanique du clonage, celle que cinq démarrages ont
  validée, au lieu d'en être une seconde copie
- `engine/backup` : un disque vers une image, la compression ne ralentissant pas la lecture

Les essais se font avec les rôles actuels du code (port 1 source, baies cibles) et le T7 comme
stockage : le choix libre des disques vient avec la phase 4.

**Fini quand** : l'aller-retour disque → image → disque redonne une arborescence identique,
dans les deux modes ; une image tronquée à la main est refusée par la vérification des
empreintes **avant** toute écriture sur les cibles ; un dossier sans `clonegator.json`
n'apparaît pas dans la liste ; une image restaurée à la main en suivant son `LISEZMOI.txt` donne
une cible conforme ; et un vrai disque restauré depuis une image démarre.

**Résultat.** `image`, `storage`, `engine/sources`, `engine/backup`, et les sous-commandes
`images`, `sauvegarder`, `restaurer`. Essais automatiques : `tests.test_layout` (sans disque) et
`tests.test_images_banc` (banc).

| Essai | Résultat |
|-------|----------|
| Windows du port 1 → image sur le T7 | 2,6 min, image de 8,94 Gio pour 23 Go utilisés |
| image → trois cibles effacées au préalable | 3 réussies en 2,1 min, dont 27 s de vérification des empreintes |
| les trois cibles démarrées sur trois machines (Kevin) | **démarrage normal** |
| contre-vérification du port 2 | partition EFI et 268 621 fichiers de C: identiques au maître |
| banc, aller-retour automatique et brut, source 1-2-3-5 | conforme au manifeste, 1-2-3-5 préservé |
| banc, image tronquée à la main | refusée avant toute écriture, fichier nommé, cible identique au bit près |
| banc, restauration à la main par le seul `LISEZMOI.txt`, disque vierge | conforme |

Ce que les essais ont appris :

- **Restaurer va plus vite que cloner** : on lit le disque d'images et `zstd -d`, rapides, au
  lieu du maître, lent. La vérification des empreintes coûte une demi-minute pour 9 Gio.
- **Le TRIM ne vide pas toujours un disque** : ces SSD ne garantissent pas de rendre des zéros
  après effacement (`DISC-ZERO` à 0). Pour qu'un essai de restauration prouve quelque chose, les
  cibles ont été effacées puis vérifiées par échantillonnage — une seconde passe a suffi, sauf
  un résidu de 0,1 % sur un disque.
- **Une image brute restaurée sur un disque plus grand** laisse l'en-tête GPT de secours là où
  finissait le disque d'origine, pas à la fin de la cible. C'est le propre d'une copie secteur
  par secteur ; le mode automatique, lui, le replace.
- **Une partition FAT presque vide se compresse à presque rien** (810 octets pour 256 Mio sur le
  banc) : ses tables sont faites de zéros.

**Reporté en phase 4, faute de matériel** : monter un disque USB de stockage qui ne l'est pas
encore, et essayer le refus FAT32 sur un vrai disque. Aujourd'hui, `storage` ne voit que les
systèmes de fichiers déjà montés — celui du T7, par exemple.

### Phase 4 — Interface et modes · taille L · **terminée le 2026-09-25**

- `devices` : les emplacements du §3.1 — SATA sur plusieurs contrôleurs, NVMe, USB ramenés à un
  connecteur physique (un port USB 3 et son jumeau USB 2 ne font qu'un) — et les deux filets de
  P2 : ouverture exclusive refusée par le noyau, images CloneGator présentes sur le disque
- mode libre et mode station (§3.2, §3.3), réglage de station enregistré dans `/etc/clonegator/`,
  assistant pré-rempli, reprise du dernier mode au redémarrage
- lancement automatique au démarrage en mode station : une unité systemd sur la console, que
  CloneGator active et désactive lui-même
- `reseau` : partage Windows (SMB) monté par `mount.cifs` ; le mot de passe passe par un fichier
  temporaire lisible par root seul, jamais par la ligne de commande — que `sysexec` journalise
  mot pour mot
- `storage` : monter un disque USB de stockage qui ne l'est pas, et refuser un FAT32 sur un vrai
  disque (reporté de la phase 3)
- l'écran de confirmation annonce les partitions copiées intégralement, Windows mal arrêté en
  tête (§6.2, §9.5)
- `ui/model` d'abord, testable et imprimable en texte brut
- `ui/screens` ensuite : accueil, listes de disques, confirmation, progression, rapport, mode
  station et journaux (§9) ; un rapport conservé avec chaque journal d'opération
- l'écran de rapport qui ne s'efface jamais tout seul (§9.7)
- le banc simulé, ici seulement, pour produire à volonté les états qu'on ne peut pas provoquer :
  disque défaillant au SMART, cible trop petite, six baies pleines
- rafraîchissement du tableau à l'insertion et au retrait d'un disque — testé en débranchant
- `health` : SMART sur de vrais disques usagés
- comportement au retrait à chaud d'une cible en cours de copie (§13)

**Fini quand** : le parcours complet — inventaire, confirmation, progression, rapport — tourne
sur la station A, dans les deux modes ; le réglage de station survit à un redémarrage et se
quitte, et son lancement automatique fonctionne ; en mode libre, une image se restaure vers un
disque USB et le disque système n'est jamais proposé ; une sauvegarde et une restauration passent
par le partage réseau ; et le retrait volontaire d'une cible en cours de copie est signalé à l'écran
sans perturber les autres.

**Résultat.** `devices` réécrit autour des emplacements et des filets, `montage`, `config`,
`reseau`, `storage`, `cache`, `health`, `demarrage`, `verrou`, `texte`, et l'interface
(`ui/model`, `ui/ecran`, `ui/app`) ; `python3 -m clonegator` l'ouvre. Essais automatiques :
`tests.test_emplacements` (machine simulée : deux contrôleurs SATA, NVMe, USB 3 et son jumeau,
concentrateur), `tests.test_sante`, `tests.test_interface`. Le banc simulé prévu ici n'a pas été
nécessaire : ces essais sans disque couvrent les états qu'il devait produire.

| Essai, dans l'interface | Résultat |
|-------------------------|----------|
| clonage SATA1 → SATA2, SATA3 | 2 réussies, 2 min 31 s |
| sauvegarde vers le partage réseau | 9,6 Go en 2 min 39 s ; mot de passe ni enregistré ni journalisé |
| restauration depuis le partage → SATA4, SATA5 | 2 réussies, 3 min 54 s |
| mode station : assistant, accueil, raccourcis, sortie, retour pré-rempli | conforme |
| retrait d'un disque de la station | le tableau le montre vide, puis revenu, sans rien toucher |
| retrait à chaud d'une cible en pleine copie, vers cinq | échec nommé en moins de 5 s, les quatre autres réussies en 2,5 min |
| lancement automatique, démarré à la main | l'accueil du mode station sur tty1, lu sur `/dev/vcs1` |
| arrêt de la machine pendant un clonage (SIGTERM) | cibles INTERROMPUES, rapport écrit, source rendue, sortie propre |
| disque USB branché non monté, choisi pour une sauvegarde | monté par CloneGator, 9,6 Go en 2 min 26 s, démonté ensuite |
| ce disque, portant désormais des sauvegardes | refusé comme cible : « contient des sauvegardes CloneGator » |
| ce disque reformaté en FAT32 | refusé comme disque de sauvegardes, avec le motif |
| restauration vers ce disque USB, en mode libre | 1 réussie en 2 min 52 s, fichiers identiques au maître |

Ce que les essais ont appris :

- **La réserve d'écriture du noyau est commune à toute la machine.** Un disque retiré en pleine
  copie n'écrit plus rien, mais le noyau continue d'accepter ce qu'on lui envoie : 7 Go en
  attente ont gelé toutes les écritures, et le moteur a abandonné les quatre cibles *saines*
  pendant que la cible retirée se traînait. Deux remèdes : un plafond par disque (`max_bytes`
  512 Mio, `strict_limit`), qui a même accéléré le groupe (237 Mo/s contre 226), et une
  surveillance de la présence de chaque cible toutes les 2 s.
- **Une synchronisation finale peut durer des minutes** : 207 s pour vider les Go gardés en
  attente vers un partage à 36 Mo/s, prise pour un blocage. Vers un fichier, le noyau est invité à
  écrire au fil de l'eau (`fadvise` toutes les 64 Mio) : 22 s. Pas vers les disques, où cela
  coûtait 8 %. Et la synchronisation finale a droit à un délai de grâce.
- **Le partage réseau d'essai** écrit entre 36 et 69 Mo/s et lit à 56 Mo/s : moins que le
  gigabit ne le laisse espérer, le serveur fixe le rythme.
- **Une unité systemd sur tty1** doit démarrer *après* l'invite de connexion qu'elle remplace
  (`After=getty@tty1.service`), sinon la fermeture de sa session raccroche le terminal.
- **La police de la console** (Uni2-Fixed16) n'a pas « ⚠ ». Accents, flèches et « ✗ » y sont.

**Reste, faute de matériel :**

- SMART sur un disque réellement usé ou défaillant (les sept de la station sont sains).

**Transition jusqu'ici.** Tant que la phase 4 n'est pas faite, `devices.role` applique les
règles de la révision 0.3 de l'analyse : port 1 source, autres ports SATA cibles, USB jamais
cloné. C'est exactement le réglage de la station de développement, un cas particulier du mode
station ; la phase 4 le remplace, elle ne l'enrichit pas.

### Phase 5 — Paquet et recette · taille S · **sur la station B** · **terminée le 2026-09-25**

- `.deb`, unité systemd, `/etc`, `/var/log`
- un README qui tient en un écran

**Fini quand** : la station B, qui n'a jamais servi au développement, s'installe depuis le
paquet seul, démarre sur l'interface, et clone un disque qui démarre.

**Où on en est (2026-09-25).** `./outils/construire-paquet.sh` construit
`dist/clonegator_<version>_all.deb` avec `dpkg-deb` seul ; la version porte la date et le
commit, et c'est elle que le logiciel affiche. Sur la station A : installation par `apt`
(dépendances résolues), commande `clonegator` utilisable de partout, unité du mode station
pointant sur `/usr/bin/clonegator`, désinstallation qui rend la console à son invite de
connexion. Redémarrage réel en lancement automatique depuis le paquet installé, fait par Kevin
le 2026-09-25 : la station s'ouvre sur l'accueil du mode station.

**Recette (Kevin, 2026-09-25)** : sur un Ubuntu neuf, le paquet publié en release GitHub
(`github.com/kevin-belanger/CloneGator/releases/latest/download/clonegator.deb`) s'installe par
`apt`, CloneGator s'ouvre avec `sudo`, montre les ports SATA de la machine même sans baie
branchée, et mène une opération entre le partage réseau et un disque USB. Kevin juge la recette
suffisante : **phase 5 terminée, MVP livré.** Lancé sans root, CloneGator le dit désormais en
une phrase au lieu d'une trace Python.

### Phase 6 — CloneGator live · taille M

Dans le même dépôt : le live est construit à partir du paquet, il en suit la version, et il
peut demander des ajustements au logiciel.

- `outils/construire-live.sh` : `mmdebstrap` fabrique un Debian 13 minimal, y installe le
  `.deb` de `dist/`, et produit dans `dist/` l'ISO hybride et les trois fichiers du démarrage
  réseau (noyau, initrd, système compressé). Le `live-build` des dépôts Ubuntu est trop ancien
  pour Debian 13.
- menu de démarrage (GRUB en UEFI, ISOLINUX ou GRUB en BIOS) : les trois claviers, le
  premier par défaut après quelques secondes
- CloneGator ouvert d'office sur tty1, en root
- côté logiciel : savoir qu'on tourne en live, pour « Quitter » (éteindre, redémarrer,
  console) et pour ne pas poser la question du lancement automatique en mode station. Vérifier
  que la clé de démarrage, en lecture seule, n'est pas proposée comme stockage
- publier l'ISO dans la même release que le `.deb`, et la montrer sur la page Télécharger

**Essais** : QEMU sur la station A, en BIOS et en UEFI avec Secure Boot, pour itérer ; puis une
vraie clé sur de vraies machines.

**Fini quand** : l'ISO, écrite sur une clé, démarre en BIOS et en UEFI Secure Boot sur de
vraies machines, s'ouvre sur CloneGator avec le bon clavier, et y mène un clonage dont la cible
démarre, ainsi qu'une sauvegarde vers un partage réseau.

---

## 4. Chemin critique

```
Phase 0 ──┬── Phase 1 ── Phase 2 ── Phase 3 ──┐
          │                                   ├── Phase 5 ── Phase 6 ── (mode PXE, §17)
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
