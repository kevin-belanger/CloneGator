# CloneGator — Analyse fonctionnelle

Duplication et sauvegarde de disques, sur une station dédiée ou sur n'importe quel PC.
Successeur de `clonesrv`, réécrit à partir de zéro.

## Tableau de révisions

| Rév. | Date | Auteur | Changement |
|------|------|--------|------------|
| 0.1 | 2026-09-16 | Kevin + Claude | Première rédaction à partir du besoin exprimé et de l'expérience de `clonesrv` |
| 0.2 | 2026-09-16 | Kevin + Claude | Système de la station sur disque USB ; NVMe/M.2 hors périmètre définitif ; mode brut ajouté à la sauvegarde ; profil des disques traités ; pas de rotation des sauvegardes ; le disque de l'OS est une destination de sauvegarde valide |
| 0.3 | 2026-09-16 | Kevin + Claude | Aucune gestion des sauvegardes dans le MVP : le logiciel lit le disque de stockage, il n'en modifie jamais le contenu. Clavier permanent confirmé. Langage tranché : Python 3, bibliothèque standard seule. Questions ouvertes closes |
| 0.4 | 2026-09-24 | Kevin + Claude | Deux modes : libre par défaut, station en raccourci enregistré (§3). P1 porte sur la source de chaque opération ; P2 devient deux filets, disque utilisé par le système et disque d'archives ; P3 raisonne en emplacements. NVMe et USB pris en charge. Format d'image arrêté (§7.2) ; une cible de restauration est jugée sur la taille requise (§8). Un Windows mal arrêté est annoncé à la confirmation (§6.2) |

---

## 1. Contexte et objectif

CloneGator duplique et sauvegarde des disques, en milieu scolaire. Il sert dans deux situations :

- **une station de clonage** à plusieurs baies : l'opérateur place un disque maître dans une
  baie, des disques à écraser dans les autres, lance l'opération et revient plus tard ;
- **n'importe quel PC** démarré sur CloneGator, pour sauvegarder son disque vers une image ou y
  restaurer une image.

Dans les deux cas, il sait cloner un disque vers un ou plusieurs disques, **sauvegarder un disque
vers une image** stockée sur un disque USB, et **restaurer une image vers un ou plusieurs
disques** sans avoir à rebrancher le maître d'origine.

L'objectif de conception tient en une phrase : **faire ce que Clonezilla fait bien, mais sans
jamais demander à l'opérateur de choisir un moteur, un format ou un nom de périphérique.**
Le nom de travail interne du principe est « Easy Clonezilla ».

## 2. Principes directeurs

Ces principes priment sur toute fonctionnalité. Ils sont des invariants, pas des préférences.

**P1 — La source d'une opération n'est jamais écrite.**
Aucun mode, aucune option, aucune combinaison d'erreurs ne doit produire une écriture sur le
disque source : le maître d'un clonage, le disque qu'on sauvegarde. Il est mis en lecture seule
au niveau du noyau (`blockdev --setro`), lui et chacune de ses partitions, pour toute la durée de
l'opération. C'est la protection la plus importante du logiciel : si une copie échoue, on a
toujours le maître. Une restauration a pour source une image, que CloneGator ne fait que lire.

**P2 — Deux filets, et seulement deux.**
L'opérateur choisit ses disques ; deux règles l'empêchent de détruire ce qui ne doit jamais l'être :

- **un disque utilisé par le système n'est jamais ni source ni cible** : monté, en swap, membre
  d'un volume LVM ou RAID, disque de démarrage. Le noyau le dit lui-même en refusant de l'ouvrir
  en exclusivité. Cette seule règle protège le système de la station, la clé de démarrage et le
  disque d'images en cours d'utilisation ;
- **un disque qui contient des images CloneGator n'est jamais une cible**, même quand il n'est
  pas monté : c'est un disque d'archives.

Tout le reste relève du choix de l'opérateur, sur un écran de confirmation qui montre exactement
ce qui sera détruit (§9.2). Aucune autre vérification ne s'y ajoute.

**P3 — L'opérateur raisonne en emplacements.**
Chaque disque est désigné par l'endroit où il est branché — `SATA1`, `NVMe1`, `USB2` —, suivi à
titre indicatif du nom que lui donne le système : `SATA1 (sda)`. Le nom `/dev/sdX` change quand
on retire et remet les disques ; l'emplacement, non (§3.1). L'opérateur n'a jamais à saisir un
nom de périphérique.

**P4 — La vitesse prime sur l'exhaustivité.**
Aucune vérification longue n'est faite par défaut. Une copie de plusieurs heures ne sera pas
suivie d'une relecture de plusieurs heures.

**P5 — Un échec doit être visible et nommé.**
On accepte qu'une copie échoue. On n'accepte pas qu'elle échoue en silence, ni qu'un écran
annonce « terminé » alors qu'une cible est inutilisable. Chaque cible a son propre verdict.

**P6 — La station ne reste jamais bloquée.**
Toute erreur, tout arrêt, toute cible morte ramène au menu dans un état cohérent.

## 3. Emplacements et modes

### 3.1 Emplacements

Un emplacement est l'endroit où un disque est branché, tel que le système le nomme de façon
stable (`/dev/disk/by-path`), quel que soit le disque qui s'y trouve :

| Bus | Affichage | Ce qu'il désigne |
|-----|-----------|------------------|
| SATA | `SATA1`, `SATA2`… | un port d'un contrôleur SATA ; plusieurs contrôleurs sont numérotés à la suite. Un SSD M.2 SATA occupe un port SATA comme un autre |
| NVMe | `NVMe1`, `NVMe2`… | un connecteur M.2 ou PCIe de la carte mère. Un disque NVMe ne se remplace pas à chaud |
| USB | `USB1`, `USB2`… | un connecteur USB de la machine. Un connecteur USB 3 garde le même nom, qu'on y branche un appareil USB 2 ou USB 3 |

Les numéros SATA suivent ceux des ports ; sur la station, ils correspondent aux baies
sérigraphiées sur le boîtier. Les numéros USB sont propres à chaque machine : stables d'un
démarrage à l'autre, mais imprimés nulle part.

Le nombre d'emplacements n'est pas codé en dur : le logiciel découvre ceux que la machine expose,
qu'elle ait quatre, six ou huit ports SATA.

Les emplacements sont considérés comme fiables : le projet prend pour acquis que l'opérateur ne
se trompe pas de baie. L'analyse ne prévoit donc **aucune validation d'identité du disque
maître** — celui-ci change à chaque utilisation, c'est précisément le but d'une station.

### 3.2 Mode libre

C'est le mode dans lequel CloneGator démarre. À chaque opération, l'opérateur choisit :

- **cloner** : un disque source vers un ou plusieurs disques cibles (§6) ;
- **sauvegarder** : un disque vers une image (§7) ;
- **restaurer** : une image vers un ou plusieurs disques (§8).

Tous les bus sont permis, USB compris — un socle de copie USB est l'outil quotidien d'un
technicien —, dans les limites des deux filets de P2.

### 3.3 Mode station

Un raccourci pour une machine dédiée au clonage. On l'active en désignant un emplacement source
et des emplacements cibles ; ensuite, on change les disques et on lance l'opération sans rien
choisir d'autre. Trois opérations en profitent :

- **cloner** la source vers les cibles ;
- **restaurer** une image vers les cibles ;
- **sauvegarder** la source vers une image.

Le réglage est enregistré et retrouvé au redémarrage (§15). On quitte le mode station à tout
moment pour revenir au mode libre.

**Seuls les emplacements internes, SATA et NVMe, peuvent faire partie d'un réglage de station.**
En mode station, tout disque présent dans un emplacement cible est effacé sans qu'on l'ait
choisi : les baies ne servent qu'à ça, alors qu'un connecteur USB reçoit des clés, des claviers
ou le disque d'images.

La station de développement a six baies SATA : source en `SATA1`, cibles de `SATA2` à `SATA6`.

### 3.4 Stockage des images et poste de travail

Les images vivent sur un disque USB ; celui qui porte le système en est un comme un autre (§7.4).
Aucun disque interne ne sert de stockage d'images.

La machine dispose en permanence d'un écran et d'un clavier. La saisie de l'étiquette d'une
sauvegarde (§7.3) est donc possible sans contrainte.

## 4. Périmètre

### Dans le MVP

1. Clonage d'un disque vers un ou plusieurs disques
2. Sauvegarde d'un disque vers une image sur USB
3. Restauration d'une image vers un ou plusieurs disques
4. Mode libre et mode station (§3)
5. Tableau des emplacements, journalisation, rapport par cible

### Hors MVP, envisagé plus tard

- Clé ou ISO live bootable, qui porte le mode libre sur n'importe quel PC (prévue une fois le
  paquet stabilisé)
- Destination réseau des images (SMB/NFS)
- Redimensionnement de la dernière partition sur une cible plus grande
- Effacement sécurisé de disques en fin de vie
- File d'attente de tâches, plusieurs sources successives
- Gestion des sauvegardes depuis l'interface : suppression, renommage, tri

### Hors périmètre, définitivement

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

Le disque source est copié simultanément vers tous les disques cibles : ceux que l'opérateur a
choisis en mode libre, ceux des emplacements cibles en mode station. La copie est faite
**partition par partition**, et le moteur de copie est **choisi automatiquement par le logiciel
pour chaque partition**. L'opérateur ne choisit pas entre « rapide » et « brut ».

### 6.2 Sélection automatique du moteur

| Contenu de la partition | Moteur | Effet |
|-------------------------|--------|-------|
| NTFS, ext2/3/4, FAT32/16, exFAT, XFS, BTRFS, HFS+ propre | `partclone.<fs>` | Seuls les blocs utilisés sont lus et écrits |
| Swap | recréation (`mkswap`) | Aucune donnée copiée |
| Système de fichiers reconnu mais marqué sale ou hiberné | copie brute, annoncée à la confirmation | Copie intégrale de la partition |
| Chiffré (BitLocker, LUKS), inconnu, ou sans système de fichiers | copie brute | Copie intégrale de la partition |

Une partition sans système de fichiers n'est pas une anomalie : la partition réservée de Windows
n'en a jamais. Elle est copiée en brut, sans avertissement.

**Un Windows mal arrêté** est le cas sale le plus courant : Windows 10 et 11 laissent C: hiberné
après un arrêt normal, à cause du démarrage rapide. CloneGator ne lève jamais l'hibernation, ce
serait écrire sur la source (P1). L'écran de confirmation annonce la copie intégrale et sa durée —
« C: : Windows n'a pas été arrêté complètement, copie intégrale de 251 Go, environ 30 min » — ;
l'opérateur continue, ou annule pour faire un arrêt complet du Windows d'origine (Maj + Arrêter).

Sont également copiés, hors partitions :

- la table de partitions (GPT ou MBR), reproduite à l'identique, GUID compris : le chargeur de
  Windows désigne ses partitions par eux
- le code d'amorçage et l'espace précédant la première partition
- pour GPT, l'en-tête de secours repositionné correctement en fin de disque cible
- pour NTFS, le secteur d'amorçage de secours, que partclone ne copie pas

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

- disque utilisé par le système : monté, en swap, membre d'un volume, disque de démarrage (P2)
- disque contenant des images CloneGator (P2)
- cible plus petite que l'espace requis par la source
- taille de secteur logique différente de celle de la source
- erreurs de lecture/écriture au test d'ouverture

Le refus est affiché **dans le tableau des emplacements, avant même que l'opérateur lance quoi
que ce soit**, pour qu'il puisse corriger le branchement sans avoir attendu.

---

## 7. Fonctionnalité 2 — Sauvegarde vers une image

### 7.1 Comportement

Un disque — celui que l'opérateur choisit, ou la source en mode station — est écrit sous forme
d'image dans un dossier sur le disque USB de stockage. Une sauvegarde, c'est un disque et une
image. Elle reprend **exactement les deux modes du clonage** :

- **Automatique** (défaut) — partition par partition, moteur choisi par le logiciel, selon le
  tableau du §6.2. C'est le cas normal.
- **Brut intégral** — image du disque entier, secteur par secteur, pour les mêmes cas exotiques
  qu'au §6.3. Annoncé comme lent.

La compression réduit fortement une image brute, l'espace libre d'un disque étant généralement à
zéro. Elle reste malgré tout nettement plus longue à produire et plus volumineuse qu'une
sauvegarde automatique : le mode brut est une réponse à un problème, pas un réglage de confort.

### 7.2 Format d'image

Un dossier par sauvegarde, dans un répertoire `CloneGator/` à la racine du disque USB. Un humain
doit pouvoir l'ouvrir et comprendre ce qu'il contient trois ans plus tard, même sans CloneGator.

**Sauvegarde automatique :**

```
CloneGator/
  2026-09-24_1430_Win11-labo/
    clonegator.json          métadonnées, écrit en dernier (voir ci-dessous)
    LISEZMOI.txt             ce qu'est ce dossier, et comment le restaurer à la main
    disque.tete              secteurs précédant la première partition (code d'amorçage)
    disque.sfdisk            table de partitions, au format texte de sfdisk
    p1.vfat.pcl.zst          une partition par fichier : image partclone compressée
    p2.brut.zst              partition sans système de fichiers : copie brute compressée
    p3.ntfs.pcl.zst
    p3.ntfs-secours          secteur d'amorçage de secours du NTFS
    p4.ntfs.pcl.zst
    p5.ntfs.pcl.zst
    empreintes.sha256        empreintes de tous les fichiers, vérifiables par « sha256sum -c »
    clonegator.log           journal complet de la sauvegarde
```

**Sauvegarde brute intégrale :**

```
CloneGator/
  2026-09-24_1430_Disque-exotique/
    clonegator.json
    LISEZMOI.txt
    disque.brut.zst
    empreintes.sha256
    clonegator.log
```

`clonegator.json` contient le strict nécessaire :

- version du format et version de CloneGator ;
- mode (`auto` ou `brut`), date, étiquette ;
- disque d'origine : modèle, numéro de série, taille, taille de secteur — pour l'affichage ;
- **taille requise** d'une cible : l'espace qu'occupent les partitions, ou le disque entier pour
  une image brute (§8) ;
- pour chaque partition : numéro, fichier, moteur, système de fichiers, taille ; pour un swap,
  qui n'a pas de fichier, son UUID et son étiquette.

La restauration s'y réfère : elle n'a jamais à deviner le format d'une image.

Décisions de format :

- **Une image est complète quand `clonegator.json` existe.** Il est écrit en tout dernier, après
  les empreintes. Une sauvegarde interrompue laisse un dossier sans lui, qui n'apparaît jamais
  dans la liste des images — sans fichier témoin ni renommage.
- **Compression zstd** : nettement plus rapide que gzip à ratio comparable, et disponible en
  paquet Debian standard. Le niveau par défaut privilégie la vitesse et utilise tous les
  processeurs : la compression ne doit pas ralentir la lecture du disque source.
- **Un fichier par partition**, nommé avec son numéro et son type, pour qu'on comprenne le
  contenu d'un dossier sans outil.
- **La table en texte** (`sfdisk --dump`) : elle suffit à reproduire une GPT à l'identique, GUID
  compris, et se lit à l'œil.
- **Les empreintes à part**, au format standard de `sha256sum` : n'importe qui peut vérifier une
  image sans CloneGator.
- **Un `LISEZMOI.txt` par image**, avec les commandes pour la restaurer à la main
  (`zstd -dc p3.ntfs.pcl.zst | partclone.ntfs -r -s - -o …`). C'est la garantie qu'une image
  reste utilisable même si CloneGator a disparu.
- **Pas de découpage en volumes.** En contrepartie, le disque USB doit porter un système de
  fichiers acceptant les gros fichiers : CloneGator refuse explicitement un support en FAT32,
  avec un message clair plutôt qu'un échec à 4 Go.
- **Pas de compatibilité Clonezilla.** Son format, proche, est nommé d'après les `/dev/sdX` ; le
  `LISEZMOI.txt` couvre le besoin de secours.

### 7.3 Étiquette

L'opérateur saisit une étiquette courte au lancement (ex. `Win11-labo-info`). C'est la seule
saisie de texte du logiciel en fonctionnement normal. Elle est reprise dans le nom du dossier et
affichée dans la liste des sauvegardes. Une valeur par défaut est proposée à partir du modèle du
disque.

### 7.4 Sélection du support de stockage

- Tous les disques USB sont détectés automatiquement, **y compris celui qui porte le système** —
  de la station ou de la clé de démarrage : c'est une destination valide, il n'y a pas de raison
  de l'écarter.
- Un seul candidat → il est sélectionné sans question.
- Plusieurs → l'opérateur choisit dans une liste affichant modèle, taille et espace libre.
- Aucun → la sauvegarde est indisponible et le menu le dit explicitement.

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
- une sauvegarde interrompue par manque de place laisse un dossier sans `clonegator.json`,
  jamais une sauvegarde d'apparence valide (§7.2).

---

## 8. Fonctionnalité 3 — Restauration d'une image

L'opérateur parcourt les sauvegardes présentes sur le disque USB. Chacune est présentée avec son
étiquette, sa date, son mode (`auto` ou `brut`), le modèle et la taille du disque d'origine, et sa
taille sur disque. Il en choisit une, et elle est restaurée vers **les disques qu'il choisit, un
ou plusieurs** — vers les emplacements cibles en mode station —, selon exactement la même
mécanique que le clonage direct : une restauration est un clonage dont la source est l'image.

Le mode est lu dans les métadonnées et appliqué sans question : une image automatique est
restaurée partition par partition, une image brute est réécrite secteur par secteur. L'opérateur
ne choisit pas la façon de restaurer.

Contraintes supplémentaires :

- une cible est refusée si elle est plus petite que la **taille requise** par l'image, et non que
  le disque d'origine : l'image d'un disque de 500 Go dont les partitions n'en occupent que 250
  se restaure sur un disque de 256 Go. Pour une image brute, la taille requise est celle du
  disque d'origine entier ;
- **intégrité de l'image** : pour une sauvegarde automatique, les empreintes de
  `empreintes.sha256` sont vérifiées avant d'écrire quoi que ce soit — quelques dizaines de
  gigaoctets à relire, c'est rapide comparé à la restauration, et une image corrompue découverte
  après coup ne sert à rien. Pour une image brute volumineuse, la vérification est proposée mais
  peut être refusée : relire intégralement l'image doublerait la durée de l'opération, ce qui
  contredirait P4 ;
- un dossier sans `clonegator.json` n'apparaît pas dans la liste des images restaurables ;
- une image produite par une version antérieure de CloneGator doit rester restaurable ; le champ
  de version dans `clonegator.json` sert à ça.

---

## 9. Interface

### 9.1 Écran principal

Un seul écran, visible en permanence, qui montre l'état réel du matériel avant toute action :
chaque disque par son emplacement, et le disque d'images. En mode station, il indique aussi le
rôle de chaque emplacement :

```
┌─ CloneGator 1.0 ── mode station ─────────────────────────────────────────┐
│  SATA1 (sdf)  SOURCE   ST500DM002-1BD142    465,8 Go   GPT, 4 part.      │
│                                                        62,4 Go utilisés  │
│  SATA2 (sdb)  CIBLE    WD2500AAKX-001CA0    232,9 Go   trop petit        │
│  SATA3 (sdc)  CIBLE    ST500DM002-1BD142    465,8 Go   prêt              │
│  SATA4 (sdd)  CIBLE    HGST HTS725050A7     500,1 Go   SMART : usure     │
│  SATA5        CIBLE    (vide)                                            │
│  SATA6        CIBLE    (vide)                                            │
├──────────────────────────────────────────────────────────────────────────┤
│  USB1 (sda)   IMAGES   SanDisk Extreme      1,8 To     412 Go libres     │
└──────────────────────────────────────────────────────────────────────────┘

   1.  Cloner la source vers les cibles
   2.  Restaurer une image vers les cibles
   3.  Sauvegarder la source vers une image
   4.  Quitter le mode station
   5.  Journaux et diagnostics
   6.  Quitter
```

En mode libre, le même tableau liste tous les disques, sans rôle ; le menu propose de cloner un
disque, sauvegarder un disque, restaurer une image, activer le mode station, consulter les
journaux et quitter. Les disques que P2 écarte y apparaissent, avec leur motif.

L'espace utilisé du disque source est affiché parce qu'il détermine à la fois la durée de
l'opération et la place nécessaire pour une sauvegarde. C'est l'information qui manque le plus
souvent au moment de décider.

Le tableau se rafraîchit automatiquement à l'insertion ou au retrait d'un disque, sans que
l'opérateur ait à demander un rescan. Un rescan manuel reste disponible dans les diagnostics.

### 9.2 Écran de confirmation

Un seul écran avant toute écriture, dans les deux modes, listant exactement ce qui sera détruit :

- la source : l'emplacement, le modèle et le numéro de série du disque, ou l'étiquette et la date
  de l'image
- pour chaque cible : emplacement, modèle, **numéro de série**, taille
- le volume estimé à copier et une estimation de durée
- les partitions copiées intégralement et pourquoi — un Windows mal arrêté en tête (§6.2) —, avec
  la durée qu'elles ajoutent
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

- version de CloneGator, date, durée, mode
- source : emplacement, modèle, **numéro de série**, taille, table de partitions, liste des
  partitions et moteurs retenus — ou l'image, pour une restauration
- pour chaque cible : emplacement, modèle, numéro de série, taille, verdict, débit moyen
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
dans le tableau des emplacements : `ok`, `usure`, `défaillant`. Un disque déclaré défaillant par
le disque lui-même est écarté par défaut, avec possibilité de forcer.

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
- **Retrait du disque USB de stockage pendant une sauvegarde** : arrêt ; le dossier reste sans
  `clonegator.json`, donc hors de la liste des images restaurables.
- Dans tous les cas, le disque source est remis dans son état normal et le logiciel revient au
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
- configuration dans `/etc/clonegator/`, dont le réglage du mode station (§3.3)
- journaux dans `/var/log/clonegator/`
- unité systemd optionnelle pour lancer l'interface au démarrage sur la console
- dépendances déclarées par le paquet : `python3`, `partclone`, `util-linux`, `zstd`,
  `smartmontools`, `ntfs-3g`, `e2fsprogs`, `dosfstools`. Aucune bibliothèque Python tierce
  (§16), donc rien à installer hors des dépôts Debian.

**Ensuite** : une clé ou une ISO live bootable construite à partir du même paquet. Elle porte le
mode libre sur n'importe quel PC, et fige complètement l'environnement d'une station. Non
planifiée tant que le paquet n'est pas éprouvé. Le système étant déjà sur USB, la transition sera
surtout un changement de mode de mise à jour.

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
  éprouvés — `partclone`, `sfdisk`, `zstd`, `blockdev`. Rien de la copie de blocs n'est
  réimplémenté.
- La version de Python visée est celle fournie par la Debian stable retenue, pas la dernière en
  date.

**Interface texte : `curses`**, donc rien à installer non plus. Ce choix découle des écrans
décrits au §9 : un tableau des emplacements qui se rafraîchit tout seul à l'insertion d'un disque
et une ligne de progression indépendante par cible ne se font pas correctement avec `whiptail`
ou `dialog`, qui raisonnent en boîtes de dialogue successives.
