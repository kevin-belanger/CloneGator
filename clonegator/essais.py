"""Essais sur les vraies baies, pour le développement.

`essai-diffusion` éprouve le moteur de la phase 1 sur le matériel : le début
brut du disque du port 1 est diffusé vers les cibles, puis chaque cible est
relue depuis le disque et comparée à la source. **Les cibles sont écrasées.**

Ce n'est pas un clonage : aucune table de partitions n'est interprétée, on
mesure seulement que N disques reçoivent exactement ce qu'on a lu une fois, et
à quel débit.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time

from . import devices, sysexec
from .engine import fanout

Gio = 1024 * fanout.Mio


def essai_diffusion(volume: int, ports: list[int] | None, delai_blocage: float) -> int:
    disques = devices.inventaire()
    source = devices.source(disques)
    if source is None:
        print("Aucun disque dans le port 1.")
        return 1

    cibles = [
        disque for disque in devices.cibles(disques)
        if ports is None or disque.port in ports
    ]
    if not cibles:
        print("Aucune cible retenue.")
        return 1

    trop_petits = [d for d in [source, *cibles] if d.taille < volume]
    if trop_petits:
        print("Volume supérieur à la taille de : "
              + ", ".join(f"port {d.port}" for d in trop_petits))
        return 1

    print(f"Source  port {source.port}  {source.description}  s/n {source.serie}")
    for cible in cibles:
        print(f"Cible   port {cible.port}  {cible.description}  s/n {cible.serie}")
    print(f"Volume  {_taille(volume)}, délai de blocage {delai_blocage:.0f} s")
    print()

    if not devices.proteger(source):
        print("Impossible de passer le port 1 en lecture seule : abandon.")
        return 1

    try:
        diffusion, duree = _diffuser(source, cibles, volume, delai_blocage)
    finally:
        devices.liberer(source)

    print()
    print(f"Source lue une fois : {_taille(diffusion.octets_lus)} en {duree:.1f} s,"
          f" {_debit(diffusion.octets_lus / duree if duree else 0)}")
    if diffusion.motif_source:
        print(f"Source : {diffusion.motif_source}")

    print()
    print("Relecture des cibles depuis le disque…")
    relues = _relire(cibles, diffusion)

    print()
    print(f"{'Cible':<8} {'Verdict':<12} {'Écrit':>10} {'Débit':>11}  Relecture")
    reussies = 0
    for disque, cible in zip(cibles, diffusion.cibles):
        conforme = relues.get(disque.chemin) == diffusion.empreinte_source
        if cible.etat == fanout.REUSSIE and conforme:
            reussies += 1
            relecture = "identique à la source"
        elif cible.etat == fanout.REUSSIE:
            relecture = "DIFFÉRENTE DE LA SOURCE"
        else:
            relecture = cible.motif
        print(f"port {disque.port:<3} {cible.etat:<12} {_taille(cible.octets):>10}"
              f" {_debit(cible.debit):>11}  {relecture}")

    print()
    print(f"{reussies} cible(s) sur {len(cibles)} identiques à la source.")
    return 0 if reussies == len(cibles) else 2


def _diffuser(source, cibles, volume, delai_blocage):
    fd_source = sysexec.ouvrir(source.chemin)
    fds: list[int] = []
    try:
        # Le cache ne doit pas fausser la mesure : on lit vraiment le disque.
        os.posix_fadvise(fd_source, 0, 0, os.POSIX_FADV_DONTNEED)
        for cible in cibles:
            fds.append(sysexec.ouvrir(cible.chemin, ecriture=True))

        diffusion = fanout.Diffusion(
            fd_source,
            [fanout.Destination(f"port {c.port}", fd) for c, fd in zip(cibles, fds)],
            limite=volume,
            delai_blocage=delai_blocage,
            empreinte=True,
        )

        fil = threading.Thread(target=diffusion.executer, name="diffusion")
        debut = time.monotonic()
        fil.start()
        try:
            while fil.is_alive():
                fil.join(timeout=2.0)
                _afficher_progression(diffusion, volume)
        except KeyboardInterrupt:
            diffusion.arreter()
            fil.join()
        duree = time.monotonic() - debut
        print()
        return diffusion, duree
    finally:
        for fd in fds:
            os.close(fd)
        os.close(fd_source)


def _afficher_progression(diffusion: fanout.Diffusion, volume: int) -> None:
    morceaux = [f"{100 * diffusion.octets_lus / volume:5.1f} %"]
    for cible in diffusion.cibles:
        if cible.active:
            morceaux.append(f"{cible.nom} {cible.debit / 1e6:4.0f} Mo/s")
        else:
            morceaux.append(f"{cible.nom} {cible.etat}")
    print("\r" + "  ".join(morceaux), end="", flush=True)


def _relire(cibles, diffusion) -> dict[str, str]:
    """Empreinte des `octets_lus` premiers octets de chaque cible, lus en parallèle."""
    resultats: dict[str, str] = {}

    def relire(disque):
        try:
            resultats[disque.chemin] = _empreinte(disque.chemin, diffusion.octets_lus)
        except OSError as erreur:
            resultats[disque.chemin] = f"relecture impossible : {erreur}"

    fils = [
        threading.Thread(target=relire, args=(disque,))
        for disque, cible in zip(cibles, diffusion.cibles)
        if cible.etat == fanout.REUSSIE
    ]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()
    return resultats


def _empreinte(chemin: str, octets: int) -> str:
    fd = sysexec.ouvrir(chemin)
    try:
        # Sans ceci, on relirait ce qui vient d'être écrit depuis la mémoire.
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        hachage = hashlib.sha256()
        reste = octets
        while reste > 0:
            bloc = os.read(fd, min(4 * fanout.Mio, reste))
            if not bloc:
                break
            hachage.update(bloc)
            reste -= len(bloc)
        return hachage.hexdigest()
    finally:
        os.close(fd)


def _taille(octets: float) -> str:
    return f"{octets / Gio:.2f} Gio".replace(".", ",")


def _debit(octets_par_seconde: float) -> str:
    return f"{octets_par_seconde / 1e6:.0f} Mo/s"
