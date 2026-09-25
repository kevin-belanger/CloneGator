"""Un seul CloneGator à la fois sur une machine.

Deux instances — l'interface sur la console d'une station et une autre ouverte
par SSH, par exemple — pourraient écrire les mêmes disques en même temps :
l'ouverture exclusive d'une cible ne l'empêche pas, puisque partclone ouvre les
partitions sans elle. Le verrou est tenu tant que le processus vit, et libéré
par le noyau à sa mort, même brutale.
"""

from __future__ import annotations

import fcntl
import os

from . import montage


def prendre():
    """Le verrou, à garder ouvert jusqu'à la fin ; None s'il est déjà tenu."""
    os.makedirs(montage.RACINE, exist_ok=True)
    fichier = open(os.path.join(montage.RACINE, "clonegator.verrou"), "w")
    try:
        fcntl.flock(fichier, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fichier.close()
        return None
    fichier.write(f"{os.getpid()}\n")
    fichier.flush()
    return fichier
