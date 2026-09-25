"""Partage réseau Windows (SMB) pour les sauvegardes (§7.4 de l'analyse).

Le partage est monté par `mount.cifs`, sous `/run/clonegator/reseau`, le temps
d'une opération.

**Le mot de passe n'apparaît nulle part** : ni dans les réglages, ni dans la
ligne de commande — que `sysexec` note mot pour mot dans le journal, et que
n'importe quel `ps` montrerait. Il passe par un fichier d'identifiants créé
lisible par root seul, effacé dès le partage monté ou refusé.
"""

from __future__ import annotations

import logging
import os
import tempfile

from . import montage, sysexec
from .config import ConnexionReseau

_log = logging.getLogger("clonegator.reseau")

POINT = montage.point("reseau")


class ErreurReseau(Exception):
    pass


def monter(connexion: ConnexionReseau, mot_de_passe: str) -> str:
    """Monte le partage et rend son point de montage. Lève ErreurReseau, avec un
    motif compréhensible, s'il est injoignable ou refuse la connexion."""
    if not connexion.renseignee:
        raise ErreurReseau("hôte ou partage non renseigné")
    demonter()  # un reste d'une opération précédente
    os.makedirs(montage.RACINE, exist_ok=True)
    os.makedirs(POINT, exist_ok=True)

    utilisateur, domaine = connexion.utilisateur, ""
    if "\\" in utilisateur:  # DOMAINE\utilisateur, à la manière de Windows
        domaine, utilisateur = utilisateur.split("\\", 1)

    fd, identifiants = tempfile.mkstemp(prefix="identifiants-", dir=montage.RACINE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fichier:  # créé en 0600 par mkstemp
            fichier.write(f"username={utilisateur}\npassword={mot_de_passe}\n")
            if domaine:
                fichier.write(f"domain={domaine}\n")
        resultat = sysexec.executer(
            ["mount", "-t", "cifs", f"//{connexion.hote}/{connexion.partage}", POINT,
             "-o", f"credentials={identifiants},uid=0,gid=0,file_mode=0600,dir_mode=0700"],
            delai=60,
        )
    finally:
        os.remove(identifiants)

    if not resultat.ok:
        raise ErreurReseau(_motif(resultat, connexion))
    _log.info("partage %s monté sur %s", connexion.unc, POINT)
    return POINT


def demonter() -> None:
    if os.path.ismount(POINT):
        montage.demonter(POINT)


def _motif(resultat: sysexec.Resultat, connexion: ConnexionReseau) -> str:
    """Le message de mount.cifs, traduit pour l'opérateur."""
    texte = f"{resultat.erreur} {resultat.sortie}"
    if resultat.expire:
        return f"{connexion.hote} ne répond pas"
    if "error(13)" in texte:
        return f"identifiants refusés par {connexion.hote}"
    if "error(2)" in texte or "error(6)" in texte:
        return f"partage « {connexion.partage} » introuvable sur {connexion.hote}"
    if any(code in texte for code in ("error(113)", "error(112)", "error(101)", "could not connect")):
        return f"{connexion.hote} injoignable"
    if "error(11)" in texte or "error(115)" in texte:
        return f"{connexion.hote} ne répond pas"
    lignes = [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]
    return lignes[-1] if lignes else "montage du partage impossible"
