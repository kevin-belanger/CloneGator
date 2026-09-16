# CloneGator — Analyse fonctionnelle

Station de duplication et de sauvegarde de disques.
Successeur de `clonesrv`, réécrit à partir de zéro.

## Tableau de révisions

| Rév. | Date | Auteur | Changement |
|------|------|--------|------------|
| 0.1 | 2026-09-16 | Kevin + Claude | Première rédaction à partir du besoin exprimé et de l'expérience de `clonesrv` |
| 0.2 | 2026-09-16 | Kevin + Claude | Système de la station sur disque USB ; NVMe/M.2 hors périmètre définitif ; mode brut ajouté à la sauvegarde ; profil des disques traités ; pas de rotation des sauvegardes ; le disque de l'OS est une destination de sauvegarde valide |
| 0.3 | 2026-09-16 | Kevin + Claude | Aucune gestion des sauvegardes dans le MVP : le logiciel lit le disque de stockage, il n'en modifie jamais le contenu. Clavier permanent confirmé. Langage tranché : Python 3, bibliothèque standard seule. Questions ouvertes closes |

---

## 1. Contexte et objectif

Une station physique dédiée, utilisée en milieu scolaire, permet de dupliquer des disques
rapidement et en série. L'opérateur place un disque maître dans le port SATA 1 du boîtier,
place jusqu'à cinq disques vierges dans les ports suivants, lance l'opération et revient plus
tard.

CloneGator ajoute à ce fonctionnement la possibilité de **sauvegarder un disque vers une image**
stockée sur un disque USB, et de **restaurer cette image vers plusieurs disques** sans avoir à
rebrancher le maître d'origine.

L'objectif de conception tient en une phrase : **faire ce que Clonezilla fait bien, mais sans
jamais demander à l'opérateur de choisir un moteur, un format ou un périphérique.**
Le nom de travail interne du principe est « Easy Clonezilla ».

## 2. Principes directeurs

Ces principes priment sur toute fonctionnalité. Ils sont des invariants, pas des préférences.

**P1 — Le port SATA 1 n'est jamais écrit.**
Aucun mode, aucune option, aucune combinaison d'erreurs ne doit produire une écriture sur le
disque du port 1. Le disque y est mis en lecture seule au niveau du noyau (`blockdev --setro`)
dès sa détection et pour toute la durée d'une opération. C'est la protection la plus importante
du logiciel : si une copie échoue, on a toujours le maître.

**P2 — Le clonage vit sur SATA, le stockage vit sur USB.**
**Aucun disque USB n'est jamais cloné**, ni comme source ni comme cible — et aucun disque SATA
n'est un support d'images. Cette seule règle suffit : elle rend impossible d'écraser un disque
d'archives, et elle protège au passage le système de la station, qui est lui aussi sur USB. Le
logiciel n'a donc besoin d'aucune détection particulière du disque système, et rien n'empêche
d'y ranger des sauvegardes comme sur n'importe quel autre disque USB.

**P3 — L'opérateur ne manipule jamais de `/dev/sdX`.**
Il raisonne en numéros de port, tels qu'ils sont sérigraphiés sur le boîtier. Le logiciel ne
propose jamais une liste de périphériques bruts.

**P4 — La vitesse prime sur l'exhaustivité.**
Aucune vérification longue n'est faite par défaut. Une copie de plusieurs heures ne sera pas
suivie d'une relecture de plusieurs heures.

**P5 — Un échec doit être visible et nommé.**
On accepte qu'une copie échoue. On n'accepte pas qu'elle échoue en silence, ni qu'un écran
annonce « terminé » alors qu'une cible est inutilisable. Chaque cible a son propre verdict.

**P6 — La station ne reste jamais bloquée.**
Toute erreur, tout arrêt, toute cible morte ramène au menu dans un état cohérent.

## 3. Matériel et topologie

| Rôle | Bus | Emplacement | Accès |
|------|-----|-------------|-------|
| Source | SATA | Port 1 | Lecture seule, toujours |
| Cibles | SATA | Ports 2 à 6 | Écriture, après confirmation |
| Système de la station | USB | — | Jamais cloné ; peut aussi héberger des images |
| Stockage d'images | USB | — | Lecture et écriture |

Les ports SATA sont identifiés physiquement sur le boîtier, et cette identification est
considérée comme fiable : le projet prend pour acquis que l'opérateur ne se trompe pas de port.
L'analyse ne prévoit donc **aucune validation d'identité du disque maître** — celui-ci change à
chaque utilisation, c'est précisément le but de la station.

Le nombre de ports SATA n'est pas codé en dur : le logiciel découvre les ports disponibles et
s'adapte, que la carte mère en propose quatre, six ou huit.

La station dispose en permanence d'un écran et d'un clavier. La saisie de l'étiquette d'une
sauvegarde (§7.3) est donc possible sans contrainte.

**Aucun disque NVMe ni M.2 n'est géré**, ni comme source, ni comme cible, ni comme stockage.
Tous les disques manipulés sont SATA ou USB, donc nommés `/dev/sdX`, et la nomenclature des
partitions est uniforme (`/dev/sdX1`, `/dev/sdX2`…). Cette contrainte simplifie réellement le
code et est assumée.

## 4. Périmètre

### Dans le MVP

1. Clonage disque → 1 à 5 disques
2. Sauvegarde disque → image sur USB
3. Restauration image USB → 1 à 5 disques
4. Tableau d'état des ports, journalisation, rapport par cible

### Hors MVP, envisagé plus tard

- ISO live bootable (prévue une fois le paquet stabilisé)
- Destination réseau des images (SMB/NFS)
- Redimensionnement de la dernière partition sur une cible plus grande
- Effacement sécurisé de disques en fin de vie
- File d'attente de tâches, plusieurs sources successives
- Gestion des sauvegardes depuis l'interface : suppression, renommage, tri

### Hors périmètre, définitivement

- Disques NVMe et M.2
- Rotation ou suppression **automatique** des sauvegardes : le logiciel ne supprime jamais rien
  de son propre chef, quelle que soit la version

---

## 5. Profil des disques traités

Le cas courant est un disque de **500 Go contenant peu de données réelles** — souvent moins du
cinquième de sa capacité. Des exceptions existent : disques plus gros, disques réellement remplis.

Ce profil justifie plusieurs choix de l'analyse :

- La copie par **blocs utilisés est le cas normal, pas une optimisation**. Sur un 500 Go dont
  60 Go sont occupés, on lit 60 Go au lieu de 500. C'est ce qui fait tenir une duplication dans
  une pause plutôt que dans une soirée.
- La **copie brute intégrale est l'exception**, et c'est pourquoi elle est toujours annoncée
  comme lente, partout où elle est proposée.
- Une sauvegarde compressée typique pèse quelques dizaines de gigaoctets. Un disque USB de 1 To
  en héberge une bonne dizaine, ce qui rend acceptable l'absence de rotation automatique.
- Un fichier d'image dépasse malgré tout 4 Go dès le premier cas courant. D'où le refus d'un
  support de stockage en FAT32 et l'absence de découpage en volumes.

---

## 6. Fonctionnalité 1 — Clonage direct

### 6.1 Comportement

Le disque du port 1 est copié simultanément vers tous les disques détectés sur les ports 2 à 6.
La copie est faite **partition par partition**, et le moteur de copie est **choisi
automatiquement par le logiciel pour chaque partition**. L'opérateur ne choisit pas entre
« rapide » et « brut ».

### 6.2 Sélection automatique du moteur

| Contenu de la partition | Moteur | Effet |
|-------------------------|--------|-------|
| NTFS, ext2/3/4, FAT32/16, exFAT, XFS, BTRFS, HFS+ propre | `partclone.<fs>` | Seuls les blocs utilisés sont lus et écrits |
| Swap | recréation (`mkswap`) | Aucune donnée copiée |
| Système de fichiers reconnu mais marqué sale | copie brute, avec avertissement | Copie intégrale de la partition |
| Chiffré (BitLocker, LUKS), inconnu, ou sans système de fichiers | copie brute | Copie intégrale de la partition |

Sont également copiés, hors partitions :

- la table de partitions (GPT ou MBR), reproduite à l'identique
- le code d'amorçage et l'espace précédant la première partition
- pour GPT, l'en-tête de secours repositionné correctement en fin de disque cible

L'espace non alloué n'est pas copié.

### 6.3 Mode brut intégral

Une option explicite, présentée comme lente, permet de forcer une copie brute du disque entier,
secteur par secteur, sans interprétation des partitions. Elle sert aux cas exotiques :
partitionnement non reconnu, disque endommagé dont on veut une image fidèle, chiffrement de
disque complet.

Dans ce mode, aucune vérification n'est faite, ni pendant ni après. C'est un choix assumé :
l'opération dure déjà plusieurs heures.

### 6.4 Comportement multi-cibles

- La source est lue **une seule fois** et diffusée vers toutes les cibles en parallèle.
- Le débit global doit être celui de la cible la plus lente, pas moins. Un tampon indépendant par
  cible évite qu'un disque lent ne bride les autres.
- **L'échec d'une cible n'interrompt pas les autres.** La cible est marquée en échec, la copie
  continue sur les cibles restantes.
- Une cible qui cesse de progresser pendant un délai configurable est déclarée bloquée, abandonnée,
  et signalée — la station ne reste pas figée dessus.

### 6.5 Validation préalable

Toutes les cibles sont validées **avant qu'une seule ne soit touchée**. Une cible refusée est
écartée de la liste et signalée ; les autres continuent. Motifs de refus :

- cible plus petite que l'espace requis par la source
- partition de la cible montée, ou utilisée comme swap
- taille de secteur logique différente de celle de la source
- erreurs de lecture/écriture au test d'ouverture

Le refus est affiché **dans le tableau des ports, avant même que l'opérateur lance quoi que ce
soit**, pour qu'il puisse corriger le branchement sans avoir attendu.

---

## 7. Fonctionnalité 2 — Sauvegarde vers une image

### 7.1 Comportement

Le disque du port 1 est écrit sous forme d'image dans un dossier sur le disque USB de stockage.
La sauvegarde reprend **exactement les deux modes du clonage** :

- **Automatique** (défaut) — partition par partition, moteur choisi par le logiciel, selon le
  tableau du §6.2. C'est le cas normal.
- **Brut intégral** — image du disque entier, secteur par secteur, pour les mêmes cas exotiques
  qu'au §6.3. Annoncé comme lent.

La compression réduit fortement une image brute, l'espace libre d'un disque étant généralement à
zéro. Elle reste malgré tout nettement plus longue à produire et plus volumineuse qu'une
sauvegarde automatique : le mode brut est une réponse à un problème, pas un réglage de confort.

### 7.2 Format d'image

Un dossier par sauvegarde, dans un répertoire `CloneGator/` à la racine du disque USB.

**Sauvegarde automatique :**

```
CloneGator/
  2026-09-16_1432_Win11-labo-info/
    clonegator.json          métadonnées : version, mode, date, étiquette, disque source
                             (modèle, série, taille, secteur), table, liste des partitions
    disk.sfdisk              table de partitions au format sfdisk
    disk.gpt                 sauvegarde GPT binaire (si applicable)
    disk.bootcode            secteurs précédant la première partition
    p1.vfat.pcl.zst          partitions, une par fichier, compressées
    p2.raw.zst
    p3.ntfs.pcl.zst
    p4.ntfs.pcl.zst
    checksums.sha256
    clonegator.log           journal complet de la sauvegarde
```

**Sauvegarde brute intégrale :**

```
CloneGator/
  2026-09-16_1432_Disque-exotique/
    clonegator.json
    disk.raw.zst
    checksums.sha256
    clonegator.log
```

Le champ `mode` de `clonegator.json` vaut `auto` ou `brut`. La restauration s'y réfère : elle n'a
jamais à deviner le format d'une image.

Décisions de format :

- **Compression zstd** par défaut : nettement plus rapide que gzip à ratio comparable, et
  disponible en paquet Debian standard. Le niveau est réglable, la valeur par défaut privilégie
  la vitesse.
- **Un fichier par partition**, nommé avec son numéro et son type, pour qu'on comprenne le
  contenu d'un dossier sans outil.
- **Pas de découpage en volumes.** En contrepartie, le disque USB doit porter un système de
  fichiers acceptant les gros fichiers : CloneGator refuse explicitement un support en FAT32,
  avec un message clair plutôt qu'un échec à 4 Go.
- **Les métadonnées sont lisibles** (JSON, texte). Un humain doit pouvoir ouvrir le dossier et
  comprendre ce qu'il contient trois ans plus tard.

### 7.3 Étiquette

L'opérateur saisit une étiquette courte au lancement (ex. `Win11-labo-info`). C'est la seule
saisie de texte du logiciel en fonctionnement normal. Elle est reprise dans le nom du dossier et
affichée dans la liste des sauvegardes. Une valeur par défaut est proposée à partir du modèle du
disque.

### 7.4 Sélection du support de stockage

- Tous les disques USB sont détectés automatiquement, **y compris celui qui porte le système de
  la station** : c'est une destination valide, il n'y a pas de raison de l'écarter.
- Un seul candidat → il est sélectionné sans question.
- Plusieurs → l'opérateur choisit dans une liste affichant modèle, taille et espace libre.
- Aucun → le mode sauvegarde est indisponible et le menu le dit explicitement.

### 7.5 Espace disponible et ménage

**Le MVP ne gère pas les sauvegardes.** Sur le disque de stockage, CloneGator écrit de nouveaux
dossiers et lit les dossiers existants — il n'en supprime, n'en renomme et n'en déplace jamais
aucun. Le ménage se fait à la main, en branchant le disque sur un autre poste. C'est un choix
délibéré : il retire du MVP toute une interface de gestion de fichiers et la seule opération
destructive qui n'aurait pas concerné un disque cible.

En contrepartie, le disque de stockage finira par se remplir, et le logiciel doit le dire **bien
avant** que ce soit un problème :

- l'espace libre du disque de stockage est affiché en permanence sur l'écran principal, pas
  seulement au moment de lancer une sauvegarde ;
- avant de démarrer, l'espace libre est comparé à l'espace utilisé du disque source (borne haute
  avant compression) ; si c'est insuffisant, l'opérateur est averti mais peut tout de même
  lancer, la compression ramenant souvent le besoin réel bien en dessous ;
- une sauvegarde interrompue par manque de place laisse un dossier marqué incomplet, jamais une
  sauvegarde d'apparence valide.

---

## 8. Fonctionnalité 3 — Restauration d'une image

L'opérateur parcourt les sauvegardes présentes sur le disque USB. Chacune est présentée avec son
étiquette, sa date, son mode (`auto` ou `brut`), le modèle et la taille du disque d'origine, et sa
taille sur disque. Il en choisit une, et elle est restaurée vers **toutes les cibles SATA
détectées**, selon exactement la même mécanique multi-cibles que le clonage direct.

Le mode est lu dans les métadonnées et appliqué sans question : une image automatique est
restaurée partition par partition, une image brute est réécrite secteur par secteur. L'opérateur
ne choisit rien.

Contraintes supplémentaires :

- une cible est refusée si elle est plus petite que le disque d'origine de l'image ;
- **intégrité de l'image** : pour une sauvegarde automatique, les empreintes de `checksums.sha256`
  sont vérifiées avant d'écrire quoi que ce soit — quelques dizaines de gigaoctets à relire, c'est
  rapide comparé à la restauration, et une image corrompue découverte après coup ne sert à rien.
  Pour une image brute volumineuse, la vérification est proposée mais peut être refusée : relire
  intégralement l'image doublerait la durée de l'opération, ce qui contredirait P4 ;
- un dossier marqué incomplet n'apparaît pas dans la liste des images restaurables ;
- une image produite par une version antérieure de CloneGator doit rester restaurable ; le champ
  de version dans `clonegator.json` sert à ça.

---

## 9. Interface

### 9.1 Écran principal

Un seul écran, visible en permanence, qui montre l'état réel du matériel avant toute action :

```
┌─ CloneGator 1.0 ────────────────────────────────────────────────────┐
│  Port 1   SOURCE    ST500DM002-1BD142    465,8 Go   GPT, 4 part.    │
│                                                     62,4 Go utilisés│
│  Port 2   CIBLE     WD2500AAKX-001CA0    232,9 Go   trop petit      │
│  Port 3   CIBLE     ST500DM002-1BD142    465,8 Go   prêt            │
│  Port 4   CIBLE     HGST HTS725050A7     500,1 Go   SMART : usure   │
│  Port 5   CIBLE     (vide)                                          │
│  Port 6   CIBLE     (vide)                                          │
├─────────────────────────────────────────────────────────────────────┤
│  Images   USB       SanDisk Extreme      1,8 To     412 Go libres   │
└─────────────────────────────────────────────────────────────────────┘

   1.  Cloner le port 1 vers les cibles
   2.  Sauvegarder le port 1 vers une image
   3.  Restaurer une image vers les cibles
   4.  Journaux et diagnostics
   5.  Quitter
```

L'espace utilisé du disque source est affiché parce qu'il détermine à la fois la durée de
l'opération et la place nécessaire pour une sauvegarde. C'est l'information qui manque le plus
souvent au moment de décider.

Le tableau se rafraîchit automatiquement à l'insertion ou au retrait d'un disque, sans que
l'opérateur ait à demander un rescan. Un rescan manuel reste disponible dans les diagnostics.

### 9.2 Écran de confirmation

Un seul écran avant toute écriture, listant exactement ce qui sera détruit :

- pour chaque cible : port, modèle, **numéro de série**, taille
- le volume estimé à copier et une estimation de durée
- les cibles écartées et pourquoi

Le bouton par défaut est **Annuler**. La confirmation demande une action délibérée.

### 9.3 Écran de progression

- une ligne par cible, avec son propre pourcentage et son propre état
- l'étape en cours (préparation, partition 3 sur 4, finalisation)
- le débit courant et une estimation du temps restant
- une cible en échec ou bloquée devient immédiatement visible sans faire disparaître les autres
- l'écran reste lisible de loin : la station tourne sans surveillance rapprochée

### 9.4 Écran de rapport

Affiché à la fin et **jamais effacé automatiquement**. Verdict par cible, durée totale, chemin du
journal. C'est l'écran qu'on photographie ou qu'on note.

---

## 10. Journalisation

Chaque opération produit un journal horodaté sous `/var/log/clonegator/`, et une copie sur le
disque USB lorsqu'il est présent.

Contenu minimal :

- version de CloneGator, date, durée
- source : modèle, **numéro de série**, taille, table de partitions, liste des partitions et
  moteurs retenus
- pour chaque cible : port, modèle, numéro de série, taille, verdict, débit moyen
- toute erreur rencontrée, avec la partition et la cible concernées

Le numéro de série est le point important : c'est ce qui permet, trois semaines plus tard, de
répondre à « ce disque-là, il a été cloné correctement ? ».

L'entrée « Journaux et diagnostics » du menu permet de relire les dernières opérations sans
quitter l'interface.

---

## 11. Vérification

Conformément au principe P4, la vérification longue n'est pas le comportement par défaut.
Trois niveaux :

| Niveau | Coût | Par défaut |
|--------|------|------------|
| **Aucun** | nul | en mode brut intégral |
| **Léger** | quelques secondes | oui, en clonage et restauration automatiques |
| **Complet** | égal à la durée de la copie | non, option explicite |

La **vérification légère** consiste à relire la table de partitions de chaque cible et à la
comparer à celle attendue, puis à faire passer à chaque partition copiée un contrôle de cohérence
en lecture seule (`fsck -n` selon le type). Elle ne relit pas les données. Elle attrape les
échecs les plus fréquents — table non écrite, partition tronquée, système de fichiers
inutilisable — pour un coût négligeable.

La **vérification complète** relit intégralement les cibles et compare. Elle est proposée, jamais
imposée, et son écran annonce clairement qu'elle double la durée de l'opération.

---

## 12. Contrôle de santé des disques

Avant une opération, l'état SMART de chaque cible est interrogé (une seule commande) et résumé
dans le tableau des ports : `ok`, `usure`, `défaillant`. Un disque déclaré défaillant par le
disque lui-même est écarté par défaut, avec possibilité de forcer.

Cela coûte presque rien et a une vraie valeur sur un parc de disques recyclés : cloner sur un
disque mourant réussit aujourd'hui sans que personne ne le sache.

---

## 13. Gestion des interruptions

- **Interruption clavier ou coupure en cours d'écriture** : toutes les écritures en cours sont
  arrêtées proprement, les cibles concernées sont marquées comme **invalides** à l'écran et au
  journal. Le logiciel ne prétend jamais qu'une copie interrompue est utilisable.
- **Retrait d'un disque cible en cours d'opération** : traité comme un échec de cette cible, les
  autres continuent.
- **Retrait du disque source** : arrêt de l'opération, toutes les cibles marquées invalides.
- **Retrait du disque USB de stockage pendant une sauvegarde** : arrêt, dossier marqué incomplet
  par un fichier témoin, qui l'exclut de la liste des images restaurables.
- Dans tous les cas, le disque du port 1 est remis dans son état normal et la station revient au
  menu.

---

## 14. Exigences non fonctionnelles

- **Débit** : le logiciel ne doit pas être le facteur limitant. Le clonage vers N cibles tourne au
  débit de la cible la plus lente.
- **Démarrage** : menu affiché et disques détectés en moins de cinq secondes.
- **Langue** : interface et journaux entièrement en français.
- **Saisie** : aucune saisie de texte en fonctionnement normal, hors l'étiquette d'une sauvegarde.
- **Autonomie** : la station tourne sans surveillance ; l'écran final subsiste jusqu'à ce qu'un
  humain le lise.
- **Version affichée** : le numéro de version figure dans le menu et dans chaque journal, pour
  qu'on sache toujours quelle version a réellement tourné.
- **Une seule source de vérité** : le dépôt est versionné, l'installation se fait par paquet, et
  ce qui s'exécute correspond exactement à ce qui est publié.

---

## 15. Livraison

**MVP** : un paquet `.deb` installable sur Debian ou Ubuntu, le système de la station étant
installé sur un disque USB.

- code dans `/usr/lib/clonegator/`, commande `clonegator` dans le `PATH`
- configuration dans `/etc/clonegator/`
- journaux dans `/var/log/clonegator/`
- unité systemd optionnelle pour lancer l'interface au démarrage sur la console
- dépendances déclarées par le paquet : `python3`, `partclone`, `gdisk`, `util-linux`, `zstd`,
  `smartmontools`. Aucune bibliothèque Python tierce (§16), donc rien à installer hors des
  dépôts Debian.

**Ensuite** : une ISO live bootable construite à partir du même paquet, pour figer complètement
l'environnement. Non planifiée tant que le paquet n'est pas éprouvé. Le système étant déjà sur
USB, la transition sera surtout un changement de mode de mise à jour.

---

## 16. Langage d'implémentation

**Python 3, bibliothèque standard uniquement.** Tranché en révision 0.3.

Pourquoi, plutôt que bash comme dans `clonesrv` :

- Python 3 est présent d'office sur toute Debian et Ubuntu. Ce n'est pas une dépendance à
  installer, c'est une ligne dans le paquet.
- La bibliothèque standard couvre tout le besoin : `json` pour les métadonnées d'image,
  `subprocess` pour piloter les outils système, `threading`/`selectors` pour la diffusion vers
  plusieurs cibles et le suivi de progression indépendant, `curses` pour l'interface.
- Ce sont exactement les points où bash aurait coûté cher : état et verdict par cible,
  surveillance de blocage, écriture et relecture de JSON — cette dernière aurait imposé `jq`,
  c'est-à-dire une vraie dépendance en plus.

Ce que la décision n'autorise pas :

- **Aucune bibliothèque tierce**, ni maintenant ni plus tard : pas de `pip`, pas
  d'environnement virtuel. Si un besoin semble en réclamer une, c'est un point à discuter, pas à
  trancher en cours de route.
- **Python orchestre, il ne copie pas.** Le travail réel reste délégué aux outils système
  éprouvés — `partclone`, `sfdisk`, `sgdisk`, `zstd`, `blockdev`. Rien de la copie de blocs n'est
  réimplémenté.
- La version de Python visée est celle fournie par la Debian stable retenue, pas la dernière en
  date.

**Interface texte : `curses`**, donc rien à installer non plus. Ce choix découle des écrans
décrits au §9 : un tableau des ports qui se rafraîchit tout seul à l'insertion d'un disque et une
ligne de progression indépendante par cible ne se font pas correctement avec `whiptail` ou
`dialog`, qui raisonnent en boîtes de dialogue successives.
