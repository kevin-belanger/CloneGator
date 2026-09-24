"""Disques USB de stockage des images (§7.4 de l'analyse).

Un disque de stockage est un disque USB dont un système de fichiers est monté.
Sur ce système de fichiers, les images vivent dans `CloneGator/`, à la racine.
Le disque qui porte le système de la station en est un comme un autre.

Pour un disque qui en a plusieurs — le disque système a une partition EFI, un
/boot et une racine —, c'est celui qui offre le plus d'espace libre qui sert.

Un système de fichiers FAT est refusé explicitement : une image dépasse 4 Go dès
le premier cas courant (§5), et l'échec viendrait sinon en pleine sauvegarde.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import devices
from .devices import Disque

_FAT = frozenset({"vfat", "msdos", "fat"})


@dataclass
class Stockage:
    disque: Disque
    racine: str  # point de montage
    fstype: str
    libre: int  # octets
    refus: str = ""  # vide si utilisable

    @property
    def utilisable(self) -> bool:
        return not self.refus


def candidats() -> list[Stockage]:
    """Un candidat par disque USB portant au moins un système de fichiers monté."""
    resultats = []
    for disque in devices.stockages(devices.inventaire()):
        montes = []
        for racine, fstype in devices.montages(disque.chemin):
            try:
                etat = os.statvfs(racine)
            except OSError:
                continue
            montes.append(Stockage(disque, racine, fstype, etat.f_bavail * etat.f_frsize))
        if not montes:
            continue

        utilisables = [s for s in montes if s.fstype not in _FAT]
        if utilisables:
            resultats.append(max(utilisables, key=lambda s: s.libre))
        else:
            meilleur = max(montes, key=lambda s: s.libre)
            meilleur.refus = "système de fichiers FAT : les fichiers d'une image dépassent 4 Go"
            resultats.append(meilleur)
    return resultats
