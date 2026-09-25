"""Montages temporaires : sonder un disque, ou y écrire des sauvegardes.

Tous les points de montage de CloneGator vivent sous `/run/clonegator`, que le
système vide à chaque démarrage.

**Une sonde n'écrit jamais rien.** Monter « en lecture seule » ne suffit pas :
ext4 rejoue quand même son journal s'il en a besoin, et le rejeu écrit sur le
disque. La partition passe donc en lecture seule noyau pendant la sonde, et
chaque système de fichiers reçoit l'option qui interdit le rejeu.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

from . import sysexec

_log = logging.getLogger("clonegator.montage")

RACINE = "/run/clonegator"

# Options d'une sonde : lecture seule, sans rejeu de journal.
_OPTIONS_SONDE = {
    "ext2": "ro,noload",
    "ext3": "ro,noload",
    "ext4": "ro,noload",
    "xfs": "ro,norecovery",
    "btrfs": "ro,rescue=nologreplay",
    "ntfs": "ro",
    "vfat": "ro",
    "exfat": "ro",
}

# Nom du pilote noyau, quand il diffère du type vu par blkid.
_PILOTES = {"ntfs": "ntfs3"}


def point(nom: str) -> str:
    return os.path.join(RACINE, nom)


def sondable(fstype: str | None) -> bool:
    return (fstype or "").lower() in _OPTIONS_SONDE


def monter(partition: str, fstype: str, cible: str, *, lecture_seule: bool) -> sysexec.Resultat:
    fstype = fstype.lower()
    options = _OPTIONS_SONDE[fstype] if lecture_seule else "rw"
    os.makedirs(cible, exist_ok=True)
    return sysexec.executer(
        ["mount", "-t", _PILOTES.get(fstype, fstype), "-o", options, partition, cible],
        delai=60,
    )


def demonter(cible: str) -> sysexec.Resultat:
    resultat = sysexec.executer(["umount", cible], delai=120)
    if resultat.ok:
        try:
            os.rmdir(cible)
        except OSError:
            pass
    return resultat


@contextmanager
def sonde(partition: str, fstype: str | None):
    """Monte une partition le temps d'y regarder, sans jamais y écrire.

    Donne le point de montage, ou None si la partition ne se sonde pas.
    """
    if not sondable(fstype):
        yield None
        return
    cible = point("sonde-" + os.path.basename(partition))
    sysexec.executer(["blockdev", "--setro", partition])
    monte = False
    try:
        monte = monter(partition, fstype, cible, lecture_seule=True).ok
        yield cible if monte else None
    finally:
        if monte:
            demonter(cible)
        sysexec.executer(["blockdev", "--setrw", partition])
