# CloneGator — consignes de travail

Station de duplication de disques. Un maître dans le port SATA 1, des cibles
dans les suivants. Successeur de `clonesrv`, réécrit de zéro.

## Les deux documents font foi

- [ANALYSE-FONCTIONNELLE.md](ANALYSE-FONCTIONNELLE.md) — ce que le logiciel doit faire
- [PLAN-DE-DEVELOPPEMENT.md](PLAN-DE-DEVELOPPEMENT.md) — dans quel ordre on le construit

Ce sont des **documents vivants**. Quand une décision les contredit, les mettre
à jour — signaler la contradiction en une phrase, livrer, puis réécrire la
section concernée et ajouter une ligne au tableau de révisions. Ne jamais
laisser le code s'écarter d'une spec figée en silence, et ne pas employer le
vocabulaire de l'exception (« écart assumé », « contredit la spec »).

## Invariants

**P1 — Le port SATA 1 n'est jamais écrit.** Le disque maître y est mis en
lecture seule au niveau du noyau pendant toute opération. Si une copie échoue
ce n'est pas grave ; détruire le maître l'est.

**P2 — Aucun disque USB n'est jamais cloné**, ni comme source ni comme cible.
Le système de la station et le stockage des images vivent sur USB : ils sont
protégés par cette seule règle.

P2 est appliquée **à un seul endroit**, `Disque.role` dans
[clonegator/devices.py](clonegator/devices.py). Ne pas la dupliquer ailleurs,
ne pas ajouter de vérification par-dessus. Si un besoin semble exiger qu'un
disque USB devienne une cible, c'est une discussion, pas un correctif.

Les autres principes (P3 à P6) sont au §2 de l'analyse.

## Contraintes techniques

- **Python 3, bibliothèque standard seule.** Aucune dépendance tierce, jamais
  de `pip`, jamais d'environnement virtuel. Si un besoin semble en réclamer
  une, le signaler plutôt que de l'installer.
- **Python orchestre, il ne copie pas.** Le travail réel est délégué aux outils
  système : `partclone`, `sfdisk`, `sgdisk`, `zstd`, `blockdev`.
- **Tout appel système passe par `clonegator/sysexec.py`.** Aucun autre module
  n'appelle `subprocess` ni ne lit `/dev`, `/sys`, `/proc` directement. C'est
  ce qui donne un délai d'attente sur chaque commande et le journal verbatim.
- **Pas de numéros de partition supposés contigus.** Une source en 1, 2, 3, 5
  est un cas normal, pas une anomalie.
- **Pas de garde-fou superflu.** Quand une règle structurelle couvre déjà un
  cas, la nommer et s'arrêter là. Ce projet démarre, il ne protège rien de
  critique, et une couche de sécurité de plus l'alourdit sans rien apporter.

## Langue

Tout est en français : interface, messages, journaux, noms de variables et de
fonctions, commentaires, messages de commit. S'y tenir.

## Où on en est

Phase 0. L'inventaire fonctionne, **rien n'écrit encore sur un disque**.

```bash
python3 -m clonegator inventaire
python3 -m clonegator disques
python3 -m clonegator -v disques     # journalise chaque commande système
```

Prochaine étape : phase 1, le moteur de diffusion — une source lue une fois,
N cibles indépendantes, un verdict par cible. C'est la partie que bash faisait
mal et celle où il y a quelque chose à prouver.

## Banc d'essai

Deux niveaux. **Le banc en boucle est le niveau rapide** : quelques secondes
par cycle contre une dizaine de minutes sur un vrai 500 Go. On corrige un
bogue dessus, on valide ensuite sur les baies. Ne pas s'en passer sous
prétexte que le matériel est là.

```bash
sudo ./outils/banc.sh creer
sudo ./outils/banc.sh etat
sudo ./outils/banc.sh detruire
```

Le parc contient délibérément les cas qui échouent : cible trop petite, table
périmée, cible plus grande que la source, source numérotée 1-2-3-5. Un banc
sans ces cas ne prouve rien. En ajouter quand on en découvre d'autres.

`source-gpt.sha256` est le manifeste de référence : après un clonage, monter
la cible et relancer `sha256sum -c` dessus.

## Environnement

La station tourne sur Debian, système sur disque USB. Les disques dans les
baies sont des disques d'essai, sacrifiés par définition.

Les commandes disque exigent root. Claude Code tourne en utilisateur normal et
passe par `sudo` — si `sudo` demande un mot de passe, la commande reste
bloquée sans que personne puisse répondre.
