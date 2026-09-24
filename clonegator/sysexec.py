"""Unique point de passage vers le système.

Aucun autre module du projet n'appelle `subprocess` ni ne lit `/dev`, `/sys` ou
`/proc` directement. Tout passe par ici, ce qui donne trois choses :

  - un délai d'attente sur *chaque* commande. `clonesrv` pouvait se figer
    indéfiniment sur un disque bloqué, sans rien afficher ;
  - le journal verbatim des commandes réellement lancées ;
  - un point unique à remplacer pour faire tourner le reste du logiciel
    ailleurs que sur du vrai matériel.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

_log = logging.getLogger("clonegator.sysexec")

# Volontairement court : toutes les commandes qui passent par ici sont des
# commandes d'inspection. Les copies longues ont leur propre délai, explicite.
DELAI_DEFAUT = 30.0


@dataclass
class Resultat:
    """Ce qu'une commande externe a produit. N'est jamais une exception."""

    argv: list[str]
    code: int
    sortie: str
    erreur: str
    duree: float
    expire: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.expire

    @property
    def commande(self) -> str:
        return " ".join(self.argv)

    def __str__(self) -> str:
        if self.expire:
            etat = f"EXPIRE apres {self.duree:.1f}s"
        else:
            etat = f"code={self.code} en {self.duree:.2f}s"
        return f"{self.commande} -> {etat}"


def disponible(programme: str) -> bool:
    """Le programme est-il installé ?"""
    return shutil.which(programme) is not None


def executer(
    argv: list[str],
    delai: float = DELAI_DEFAUT,
    entree: str | None = None,
) -> Resultat:
    """Lance une commande et rapporte ce qu'elle a fait.

    Ne lève jamais d'exception sur un code de retour non nul : c'est à
    l'appelant de décider si l'échec est grave. Un dépassement de délai tue le
    processus et revient avec `expire=True`.
    """
    debut = time.monotonic()
    try:
        fin = subprocess.run(
            argv,
            input=entree,
            capture_output=True,
            text=True,
            timeout=delai,
            check=False,
        )
    except subprocess.TimeoutExpired as expiration:
        duree = time.monotonic() - debut
        resultat = Resultat(
            argv=list(argv),
            code=-1,
            sortie=_texte(expiration.stdout),
            erreur=_texte(expiration.stderr),
            duree=duree,
            expire=True,
        )
        _log.warning("%s", resultat)
        return resultat
    except FileNotFoundError:
        duree = time.monotonic() - debut
        resultat = Resultat(
            argv=list(argv),
            code=127,
            sortie="",
            erreur=f"{argv[0]} : introuvable",
            duree=duree,
        )
        _log.warning("%s", resultat)
        return resultat

    resultat = Resultat(
        argv=list(argv),
        code=fin.returncode,
        sortie=fin.stdout or "",
        erreur=fin.stderr or "",
        duree=time.monotonic() - debut,
    )

    if resultat.ok:
        _log.debug("%s", resultat)
    else:
        _log.warning("%s | %s", resultat, resultat.erreur.strip()[:200])

    return resultat


def executer_json(argv: list[str], delai: float = DELAI_DEFAUT) -> dict | None:
    """Comme `executer`, mais pour les commandes qui savent sortir du JSON.

    `lsblk --json` et `sfdisk --json` couvrent l'essentiel de nos besoins de
    lecture — c'est une des raisons du choix de Python (§16 de l'analyse).
    Revient à None si la commande échoue ou si la sortie n'est pas exploitable.
    """
    resultat = executer(argv, delai=delai)
    if not resultat.ok:
        return None

    try:
        return json.loads(resultat.sortie)
    except json.JSONDecodeError as erreur:
        _log.warning("%s : sortie JSON illisible (%s)", resultat.commande, erreur)
        return None


def lister(repertoire: str) -> list[str]:
    """Noms des entrées d'un répertoire système, triés ; vide s'il n'existe pas."""
    try:
        return sorted(os.listdir(repertoire))
    except OSError as erreur:
        _log.debug("%s illisible (%s)", repertoire, erreur)
        return []


def chemin_reel(chemin: str) -> str | None:
    """Cible finale d'un lien comme ceux de `/dev/disk/by-path`, None si illisible."""
    try:
        return os.path.realpath(chemin, strict=True)
    except OSError as erreur:
        _log.warning("lien illisible : %s (%s)", chemin, erreur)
        return None


def ouvrir(chemin: str, ecriture: bool = False) -> int:
    """Ouvre un disque ou un fichier et rend son descripteur.

    En écriture, un disque est ouvert en exclusivité (O_EXCL) : le noyau refuse
    si quelqu'un d'autre le tient déjà. Lève OSError en cas d'échec ; le
    descripteur appartient à l'appelant, qui le ferme.
    """
    if ecriture:
        drapeaux = os.O_WRONLY | os.O_EXCL
    else:
        drapeaux = os.O_RDONLY
    fd = os.open(chemin, drapeaux | os.O_CLOEXEC)
    _log.debug("ouvert %s en %s (fd %d)", chemin, "écriture" if ecriture else "lecture", fd)
    return fd


def _texte(brut: bytes | str | None) -> str:
    if brut is None:
        return ""
    if isinstance(brut, bytes):
        return brut.decode("utf-8", errors="replace")
    return brut
