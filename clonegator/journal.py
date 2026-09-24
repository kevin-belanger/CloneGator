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

from . import VERSION

RACINE = os.environ.get("CLONEGATOR_JOURNAUX", "/var/log/clonegator")


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
