"""Un plafond de données en attente d'écriture, disque par disque.

Le noyau garde en mémoire ce qu'on écrit avant de le pousser sur le disque, et
cette réserve est **commune à toute la machine** : jusqu'à 20 % de la mémoire.
Qu'une cible disparaisse en pleine copie — retirée à chaud, morte —, et ses
données ne partent plus jamais : elles s'accumulent jusqu'à remplir la réserve
commune, et le noyau freine alors *toutes* les écritures de la machine. Vu sur
les baies : quatre cibles saines arrêtées et abandonnées, pendant que la cible
retirée continuait à se traîner.

Le remède est dans le noyau : chaque disque peut recevoir son propre plafond
(`max_bytes`), appliqué même quand la machine n'est pas saturée
(`strict_limit`). Une cible morte n'emplit plus que sa part, cesse vite de
progresser, et la détection de blocage l'écarte ; les autres continuent.

Les réglages d'origine sont rendus à la fin de l'opération, quoi qu'il arrive.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

from . import sysexec

_log = logging.getLogger("clonegator.cache")

PLAFOND = 512 * 1024 * 1024


def _bdi(chemin_disque: str) -> str | None:
    """Le répertoire /sys des réglages d'écriture d'un disque (« 8:16 »)."""
    numeros = sysexec.lire(f"/sys/block/{os.path.basename(chemin_disque)}/dev")
    return f"/sys/class/bdi/{numeros}" if numeros else None


@contextmanager
def plafonner(chemins_disques: list[str], plafond: int = PLAFOND):
    """Plafonne le cache d'écriture de chaque disque le temps du bloc."""
    anciens = []
    for chemin in chemins_disques:
        bdi = _bdi(chemin)
        if bdi is None:
            continue
        avant = (sysexec.lire(f"{bdi}/max_bytes"), sysexec.lire(f"{bdi}/strict_limit"))
        if None in avant:
            continue  # noyau trop ancien : on fait sans
        if sysexec.ecrire(f"{bdi}/max_bytes", str(plafond)) and \
                sysexec.ecrire(f"{bdi}/strict_limit", "1"):
            anciens.append((bdi, avant))
    try:
        yield
    finally:
        for bdi, (max_bytes, strict) in anciens:
            if sysexec.lire(f"{bdi}/max_bytes") is None:
                continue  # le disque a disparu en route : plus rien à rendre
            sysexec.ecrire(f"{bdi}/strict_limit", strict)
            sysexec.ecrire(f"{bdi}/max_bytes", max_bytes)
