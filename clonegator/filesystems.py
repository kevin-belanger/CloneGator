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
import os
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


def secours_ntfs(chemin: str) -> tuple[int, bytes] | None:
    """Le secteur d'amorçage de secours d'un NTFS, et sa position en octets.

    NTFS en garde une copie juste après la fin du volume, hors des clusters que
    partclone transfère : il faut la recopier à part, sans quoi la cible garde
    à cet endroit ce qu'elle contenait avant et `ntfsfix` la déclare
    incohérente. Sa position se lit dans le secteur d'amorçage lui-même :
    octets par secteur à 0x0B, nombre de secteurs du volume à 0x28.
    """
    fd = sysexec.ouvrir(chemin)
    try:
        amorce = os.pread(fd, 512, 0)
        octets_par_secteur = int.from_bytes(amorce[0x0B:0x0D], "little")
        secteurs = int.from_bytes(amorce[0x28:0x30], "little")
        if octets_par_secteur not in (512, 1024, 2048, 4096):
            return None
        position = secteurs * octets_par_secteur
        secours = os.pread(fd, octets_par_secteur, position)
    finally:
        os.close(fd)
    if len(secours) != octets_par_secteur or secours[3:11] != b"NTFS    ":
        _log.warning("%s : secteur d'amorçage de secours introuvable", chemin)
        return None
    return position, secours


def volume_a_copier(partition: Partition, choix: Choix) -> int:
    """Octets que la copie de cette partition lira, pour l'estimation de durée
    de l'écran de confirmation (§9.5) : les blocs utilisés pour partclone,
    toute la partition pour une copie brute, rien pour un swap."""
    if choix.moteur in (SWAP, AUCUN):
        return 0
    if choix.moteur == BRUT:
        return partition.taille
    utilise = espace_utilise(partition)
    return utilise if utilise is not None else partition.taille


def espace_utilise(partition: Partition) -> int | None:
    """Espace occupé d'un système de fichiers, lu sans rien y écrire ; None si
    on ne sait pas le lire."""
    fstype = (partition.fstype or "").lower()
    if fstype == "ntfs":
        sortie = sysexec.executer(["ntfsinfo", "-m", partition.chemin]).sortie
        valeurs = _valeurs(sortie, ("Cluster Size", "Volume Size in Clusters", "Free Clusters"))
        if len(valeurs) == 3:
            taille_cluster, total, libres = valeurs
            return (total - libres) * taille_cluster
    elif fstype.startswith("ext"):
        sortie = sysexec.executer(["dumpe2fs", "-h", partition.chemin]).sortie
        valeurs = _valeurs(sortie, ("Block size", "Block count", "Free blocks"))
        if len(valeurs) == 3:
            taille_bloc, total, libres = valeurs
            return (total - libres) * taille_bloc
    return None


def _valeurs(sortie: str, cles: tuple[str, ...]) -> list[int]:
    """Les nombres qui suivent chaque clé « Clé: 1234 », dans l'ordre des clés."""
    trouvees = {}
    for ligne in sortie.splitlines():
        cle, _, reste = ligne.strip().partition(":")
        mots = reste.split()
        if cle.strip() in cles and mots and mots[0].isdigit():
            trouvees[cle.strip()] = int(mots[0])
    return [trouvees[cle] for cle in cles if cle in trouvees]
