# CloneGator

Duplication et sauvegarde de disques, en français, au clavier.

- **Cloner** un disque vers un ou plusieurs disques, en ne copiant que les blocs utilisés.
- **Sauvegarder** un disque vers une image, sur un disque USB ou un partage réseau Windows.
- **Restaurer** une image vers un ou plusieurs disques.
- **Mode station** : pour une machine à baies, un réglage enregistré — on change les
  disques et on lance, sans rien choisir. Lancement automatique au démarrage en option.

## Installer et lancer

```bash
apt install ./clonegator_<version>_all.deb
clonegator
```

Flèches et Entrée pour choisir (ou le numéro), Espace ou Entrée pour cocher,
Échap pour revenir. Une confirmation, « Annuler » par défaut, précède toute écriture.

## Ce qui ne peut pas arriver

- La source d'une opération n'est jamais écrite : elle passe en lecture seule noyau.
- Un disque utilisé par le système, ou qui porte des sauvegardes CloneGator, n'est
  jamais proposé comme cible.

Journaux : `/var/log/clonegator/`. Réglages : `/etc/clonegator/`.
Pour développer : [analyse](ANALYSE-FONCTIONNELLE.md), [plan](PLAN-DE-DEVELOPPEMENT.md),
et `python3 -m clonegator` depuis le dépôt.
