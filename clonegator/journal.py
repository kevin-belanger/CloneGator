"""Journal d'opération (§10 de l'analyse).

Chaque opération a son dossier sous `/var/log/clonegator/`, horodaté : le
journal lui-même, qui reçoit tout ce que le logiciel journalise pendant
l'opération — commandes système comprises, verbatim —, et la sortie d'erreur de
chaque programme lancé, `partclone` en tête.

Minimal pour l'instant : la copie sur le disque USB de stockage viendra avec la
phase 3.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from . import VERSION

RACINE = os.environ.get("CLONEGATOR_JOURNAUX", "/var/log/clonegator")
RAPPORT = "rapport.txt"


@dataclass
class Entree:
    """Une opération passée, telle que « Journaux » la présente."""

    dossier: str
    date: str  # « 2026-09-24 20:29 »
    operation: str

    @property
    def rapport(self) -> str | None:
        try:
            with open(os.path.join(self.dossier, RAPPORT), encoding="utf-8") as fichier:
                return fichier.read()
        except OSError:
            return None


def lister(limite: int = 50) -> list[Entree]:
    """Les dernières opérations, la plus récente en premier."""
    try:
        noms = sorted(os.listdir(RACINE), reverse=True)
    except FileNotFoundError:
        return []
    entrees = []
    for nom in noms:
        morceaux = nom.split("_", 2)
        if len(morceaux) != 3 or not os.path.isdir(os.path.join(RACINE, nom)):
            continue
        jour, heure, operation = morceaux
        entrees.append(Entree(os.path.join(RACINE, nom),
                              f"{jour} {heure[:2]}:{heure[2:4]}", operation))
        if len(entrees) >= limite:
            break
    return entrees


class Journal:
    def __init__(self, operation: str):
        horodatage = time.strftime("%Y-%m-%d_%H%M%S")
        self.dossier = os.path.join(RACINE, f"{horodatage}_{operation}")
        os.makedirs(self.dossier, exist_ok=True)
        self.chemin = os.path.join(self.dossier, "clonegator.log")
        self._numero = 0

        self._gestionnaire = logging.FileHandler(self.chemin, encoding="utf-8")
        self._gestionnaire.setLevel(logging.DEBUG)
        self._gestionnaire.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s : %(message)s")
        )
        racine = logging.getLogger("clonegator")
        racine.addHandler(self._gestionnaire)
        racine.setLevel(logging.DEBUG)

        self.log = logging.getLogger("clonegator.journal")
        self.log.info("CloneGator %s — %s", VERSION, operation)

    def ecrire_rapport(self, texte: str) -> None:
        """Le rapport de fin (§9.7), conservé avec le journal : c'est ce que
        l'entrée « Journaux » relit, trois semaines plus tard."""
        with open(os.path.join(self.dossier, RAPPORT), "w", encoding="utf-8") as fichier:
            fichier.write(texte.rstrip() + "\n")

    def fichier(self, nom: str) -> str:
        """Chemin d'un fichier annexe, numéroté dans l'ordre de création."""
        self._numero += 1
        return os.path.join(self.dossier, f"{self._numero:03d}_{nom}")

    def fermer(self) -> None:
        logging.getLogger("clonegator").removeHandler(self._gestionnaire)
        self._gestionnaire.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc) -> None:
        self.fermer()
