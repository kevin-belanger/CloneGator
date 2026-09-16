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

Phase 0. L'inventaire fonctionne, rien n'écrit encore sur un disque.

```bash
python3 -m clonegator inventaire
```

```bash
python3 -m clonegator disques
```

## Banc d'essai

Fabrique un parc de disques virtuels — vrais systèmes de fichiers, vrais
fichiers — sur lequel le moteur tourne comme sur du matériel, mais en quelques
secondes. Il contient délibérément les cas qui échouent : cible trop petite,
table périmée, cible plus grande que la source, et une source dont les
partitions sont numérotées 1, 2, 3, **5**.

```bash
sudo ./outils/banc.sh creer
```

```bash
sudo ./outils/banc.sh detruire
```

## Dépendances

Python 3 et sa bibliothèque standard uniquement — pas de `pip`, pas
d'environnement virtuel. Le travail réel est délégué aux outils système :

```bash
sudo apt-get install -y util-linux gdisk parted e2fsprogs dosfstools ntfs-3g partclone zstd smartmontools
```
