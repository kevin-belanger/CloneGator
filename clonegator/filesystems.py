"""Choix du moteur de copie, partition par partition (§6.2 de l'analyse).

L'opérateur ne choisit jamais : ce module regarde ce que contient chaque
partition et décide.

| Contenu                                   | Moteur          |
|-------------------------------------------|-----------------|
| système de fichiers reconnu et propre     | partclone.<fs>  |
| swap                                      | recréé (mkswap) |
| reconnu mais marqué sale ou hiberné       | copie brute     |
| chiffré, inconnu, sans système de fichier | copie brute     |
| partition étendue MBR                     | rien : la table la décrit |

L'état « sale » n'est vérifié que là où on sait le lire sans rien écrire : NTFS
(`ntfs-3g.probe --readonly`) et ext2/3/4 (`dumpe2fs -h`). Les autres systèmes
sont confiés à partclone, dont l'échec éventuel est rapporté comme tel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import sysexec
from .devices import Partition

_log = logging.getLogger("clonegator.filesystems")

PARTCLONE = "partclone"
BRUT = "brut"
SWAP = "swap"
AUCUN = "aucun"

# Type de système de fichiers vu par lsblk/blkid → programme partclone.
PROGRAMMES = {
    "ntfs": "partclone.ntfs",
    "ext2": "partclone.extfs",
    "ext3": "partclone.extfs",
    "ext4": "partclone.extfs",
    "vfat": "partclone.fat",
    "exfat": "partclone.exfat",
    "xfs": "partclone.xfs",
    "btrfs": "partclone.btrfs",
    "hfsplus": "partclone.hfsp",
}

# Codes de retour de ntfs-3g.probe, tels que ntfs-3g les définit.
_ETATS_NTFS = {
    13: "NTFS endommagé",
    14: "NTFS hiberné (Windows mis en veille prolongée ou démarrage rapide)",
    15: "NTFS non démonté proprement",
}


@dataclass
class Choix:
    moteur: str
    programme: str | None = None
    raison: str = ""  # pourquoi ce moteur, pour le journal
    avertissement: bool = False  # à montrer à l'opérateur (§6.2 : système sale)

    def __str__(self) -> str:
        texte = self.programme or self.moteur
        return f"{texte} ({self.raison})" if self.raison else texte


def choisir(partition: Partition, etendue: bool = False) -> Choix:
    if etendue:
        return Choix(AUCUN, raison="partition étendue, décrite par la table")

    fstype = (partition.fstype or "").lower()
    if fstype == "swap":
        return Choix(SWAP, "mkswap")

    programme = PROGRAMMES.get(fstype)
    if programme is None:
        if not fstype:
            motif = "aucun système de fichiers reconnu"
        else:
            motif = f"« {fstype} » non pris en charge par partclone"
        # Cas normal, pas une alerte : la partition réservée de Windows, par
        # exemple, n'a jamais de système de fichiers.
        return Choix(BRUT, raison=motif)

    salete = _salete(partition, fstype)
    if salete:
        return Choix(BRUT, raison=f"{salete} : copie intégrale", avertissement=True)

    return Choix(PARTCLONE, programme)


def _salete(partition: Partition, fstype: str) -> str:
    """Motif pour lequel ce système de fichiers ne doit pas passer par partclone,
    ou chaîne vide s'il est propre."""
    if fstype == "ntfs":
        sonde = sysexec.executer(["ntfs-3g.probe", "--readonly", partition.chemin])
        if sonde.ok:
            return ""
        return _ETATS_NTFS.get(sonde.code, f"NTFS illisible (ntfs-3g.probe : code {sonde.code})")

    if fstype.startswith("ext"):
        entete = sysexec.executer(["dumpe2fs", "-h", partition.chemin])
        for ligne in entete.sortie.splitlines():
            if ligne.startswith("Filesystem state:"):
                etat = ligne.split(":", 1)[1].strip()
                return "" if etat == "clean" else f"{fstype} dans l'état « {etat} »"
        return f"{fstype} illisible (dumpe2fs)"

    return ""
