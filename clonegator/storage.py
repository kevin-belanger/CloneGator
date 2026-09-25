"""Où sont les sauvegardes (§7.4 de l'analyse) : un disque USB, ou un partage
réseau Windows.

Un disque USB déjà monté sert tel quel ; pour un disque qui en a plusieurs — le
disque système a une partition EFI, un /boot et une racine —, c'est le système
de fichiers le plus spacieux qui sert. Un disque USB qui n'est pas monté l'est
par CloneGator, sous `/run/clonegator`, le temps de l'opération. Un partage
réseau aussi, une fois le mot de passe donné.

Un système de fichiers FAT est refusé explicitement : une image dépasse 4 Go dès
le premier cas courant (§5), et l'échec viendrait sinon en pleine sauvegarde.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from . import devices, montage, reseau
from .config import ConnexionReseau
from .devices import Disque

_log = logging.getLogger("clonegator.storage")

_FAT = frozenset({"vfat", "msdos", "fat"})
# Systèmes de fichiers sur lesquels CloneGator sait écrire des sauvegardes.
_ECRIVABLES = frozenset({"ext4", "ext3", "ext2", "exfat", "ntfs", "xfs", "btrfs"})

REFUS_FAT = "système de fichiers FAT : les fichiers d'une sauvegarde dépassent 4 Go"


class ErreurStockage(Exception):
    pass


@dataclass
class Stockage:
    nom: str  # ce qu'on affiche
    fstype: str
    racine: str | None = None  # point de montage, une fois monté
    libre: int | None = None  # octets, une fois monté
    refus: str = ""  # vide si utilisable
    disque: Disque | None = None
    partition: str | None = None  # à monter, pour un disque USB qui ne l'est pas
    connexion: ConnexionReseau | None = None  # pour un partage réseau
    _monte_ici: bool = False

    @property
    def utilisable(self) -> bool:
        return not self.refus

    @property
    def reseau(self) -> bool:
        return self.connexion is not None

    @property
    def pret(self) -> bool:
        """Monté : on peut y lister et y écrire des sauvegardes."""
        return self.racine is not None


def chemin_affiche(stockage: Stockage, chemin: str) -> str:
    """Un chemin tel que l'opérateur le retrouverait : l'adresse du partage
    Windows, ou le disque suivi du chemin depuis sa racine — jamais le point de
    montage interne de CloneGator."""
    relatif = os.path.relpath(chemin, stockage.racine) if stockage.racine else chemin
    if stockage.reseau:
        return stockage.connexion.unc + "\\" + relatif.replace("/", "\\")
    return f"{stockage.nom} : /{relatif}"


def candidats() -> list[Stockage]:
    """Les disques USB qui peuvent accueillir des sauvegardes, montés ou non."""
    resultats = []
    for disque in devices.inventaire():
        if not devices.peut_stocker(disque):
            continue
        candidat = _candidat(disque)
        if candidat is not None:
            resultats.append(candidat)
    return resultats


def partage(connexion: ConnexionReseau) -> Stockage:
    """Le partage réseau mémorisé : il ne sera monté qu'avec son mot de passe."""
    return Stockage(nom=f"Partage réseau {connexion.unc}", fstype="cifs", connexion=connexion)


def ouvrir(stockage: Stockage, mot_de_passe: str | None = None) -> Stockage:
    """Monte le stockage s'il ne l'est pas, et relève son espace libre.
    Lève ErreurStockage avec un motif compréhensible."""
    if stockage.refus:
        raise ErreurStockage(stockage.refus)
    if not stockage.pret:
        if stockage.reseau:
            try:
                stockage.racine = reseau.monter(stockage.connexion, mot_de_passe or "")
            except reseau.ErreurReseau as erreur:
                raise ErreurStockage(str(erreur)) from erreur
        else:
            point = montage.point("sauvegardes-" + os.path.basename(stockage.partition))
            resultat = montage.monter(stockage.partition, stockage.fstype, point,
                                      lecture_seule=False)
            if not resultat.ok:
                raise ErreurStockage(_motif_montage(stockage, resultat.erreur))
            stockage.racine = point
        stockage._monte_ici = True
    stockage.libre = _libre(stockage.racine)
    return stockage


def fermer(stockage: Stockage) -> None:
    """Démonte ce que CloneGator a monté lui-même, et seulement ça."""
    if not stockage._monte_ici:
        return
    if stockage.reseau:
        reseau.demonter()
    else:
        montage.demonter(stockage.racine)
    stockage.racine = None
    stockage._monte_ici = False


def _candidat(disque: Disque) -> Stockage | None:
    nom = f"{disque.libelle} {disque.description}"
    montes = []
    for racine, fstype in devices.montages(disque.chemin):
        libre = _libre(racine)
        if libre is not None:
            montes.append(Stockage(nom, fstype, racine=racine, libre=libre, disque=disque))
    if montes:
        utilisables = [s for s in montes if s.fstype not in _FAT]
        if utilisables:
            return max(utilisables, key=lambda s: s.libre)
        refuse = max(montes, key=lambda s: s.libre)
        refuse.refus = REFUS_FAT
        return refuse

    # Pas monté : la plus grande partition dont on sait écrire le système de
    # fichiers. Seulement du FAT : refusé, en le disant.
    partitions = sorted(disque.partitions, key=lambda p: p.taille, reverse=True)
    for partition in partitions:
        if (partition.fstype or "").lower() in _ECRIVABLES:
            return Stockage(nom, partition.fstype.lower(), disque=disque, partition=partition.chemin)
    for partition in partitions:
        if (partition.fstype or "").lower() in _FAT:
            return Stockage(nom, partition.fstype.lower(), disque=disque,
                            partition=partition.chemin, refus=REFUS_FAT)
    return None


def _libre(racine: str) -> int | None:
    try:
        etat = os.statvfs(racine)
    except OSError:
        return None
    return etat.f_bavail * etat.f_frsize


def _motif_montage(stockage: Stockage, erreur: str) -> str:
    if stockage.fstype == "ntfs":
        # Le pilote refuse d'écrire sur un NTFS que Windows n'a pas libéré.
        return ("NTFS non démonté proprement : rebrancher le disque sur Windows et "
                "l'éjecter avant de le retirer")
    lignes = [ligne.strip() for ligne in erreur.splitlines() if ligne.strip()]
    return "montage impossible : " + (lignes[-1] if lignes else "sans message")
