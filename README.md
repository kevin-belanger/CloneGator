# CloneGator

Station de duplication et de sauvegarde de disques. Successeur de `clonesrv`.

Un disque maître dans le port SATA 1, des disques vierges dans les suivants, et
on lance. Le logiciel choisit seul le moteur de copie pour chaque partition —
l'opérateur ne manipule jamais de `/dev/sdX`.

- [Analyse fonctionnelle](ANALYSE-FONCTIONNELLE.md) — ce que le logiciel doit faire
- [Plan de développement](PLAN-DE-DEVELOPPEMENT.md) — dans quel ordre on le construit

Les deux documents sont vivants : quand une décision les contredit, on les met à
jour plutôt que de laisser le code s'en écarter en silence.

## La règle porteuse

**Aucun disque USB n'est jamais cloné, ni comme source ni comme cible.** Le
système de la station et le stockage des images vivent sur USB ; ils sont donc
protégés par la règle même qu'on écrit, sans vérification supplémentaire.

Elle est appliquée à un seul endroit — l'attribution du rôle dans
[`clonegator/devices.py`](clonegator/devices.py). Ne pas la contourner ailleurs.

## État

Phase 2 terminée : le clonage direct fonctionne. Un disque Windows cloné vers
cinq cibles démarre sur cinq machines. La source est lue une seule fois, chaque
partition est copiée avec le moteur choisi pour elle, et chaque cible reçoit son
propre verdict. Sauvegarde et restauration d'images : phase 3.

```bash
python3 -m clonegator cloner
```

```bash
python3 -m clonegator inventaire
```

```bash
python3 -m clonegator disques
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
