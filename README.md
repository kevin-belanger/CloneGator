# CloneGator

Station de duplication et de sauvegarde de disques. Successeur de `clonesrv`.

Un disque maître dans le port SATA 1, des disques vierges dans les suivants, et
on lance. Le logiciel choisit seul le moteur de copie pour chaque partition —
l'opérateur ne manipule jamais de `/dev/sdX`.

- [Analyse fonctionnelle](ANALYSE-FONCTIONNELLE.md) — ce que le logiciel doit faire
- [Plan de développement](PLAN-DE-DEVELOPPEMENT.md) — dans quel ordre on le construit

Les deux documents sont vivants : quand une décision les contredit, on les met à
jour plutôt que de laisser le code s'en écarter en silence.

## Deux modes, deux filets

**Mode libre**, par défaut : on choisit ses disques à chaque opération —
cloner, sauvegarder vers une image, restaurer une image. **Mode station** : un
raccourci enregistré pour une machine à baies ; on change les disques et on
lance, sans rien choisir.

Les disques sont désignés par leur emplacement (`SATA1`, `NVMe1`, `USB2`), pas
par leur nom `/dev/sdX`, qui change au rebranchement.

La source d'une opération n'est jamais écrite. Et deux filets seulement : un
disque utilisé par le système n'est jamais ni source ni cible, un disque qui
contient des images CloneGator n'est jamais une cible. Ces règles vivent à un
seul endroit, [`clonegator/devices.py`](clonegator/devices.py).

## État

Phase 3 terminée : cloner, sauvegarder vers une image et restaurer fonctionnent,
et les disques obtenus démarrent. L'interface curses et le choix libre des
disques viennent en phase 4 ; en attendant, des sous-commandes :

```bash
python3 -m clonegator inventaire
python3 -m clonegator cloner
python3 -m clonegator sauvegarder Win11-labo
python3 -m clonegator images
python3 -m clonegator restaurer 2026-09-24_2029_Win11-labo
```

## Banc d'essai

Les essais se font sur les vraies baies. Le banc fabrique ce qu'elles ne donnent
pas sans toucher au disque maître : des disques virtuels — vrais systèmes de
fichiers, vrais fichiers — avec les cas qui échouent : cible trop petite, table
périmée, et une source dont les partitions sont numérotées 1, 2, 3, **5**.

Le banc a besoin des droits root (`losetup`, `mkfs`, `mount`).

```bash
./outils/banc.sh creer
```

```bash
./outils/banc.sh detruire
```

## Dépendances

Python 3 et sa bibliothèque standard uniquement — pas de `pip`, pas
d'environnement virtuel. Le travail réel est délégué aux outils système :

```bash
apt-get install -y util-linux gdisk parted e2fsprogs dosfstools ntfs-3g partclone zstd smartmontools
```
