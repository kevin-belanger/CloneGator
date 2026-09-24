"""Essais sur les vraies baies, pour le développement.

`essai-diffusion` éprouve le moteur de la phase 1 sur le matériel : le début
brut du disque du port 1 est diffusé vers les cibles, puis chaque cible est
relue depuis le disque et comparée à la source. **Les cibles sont écrasées.**

Ce n'est pas un clonage : aucune table de partitions n'est interprétée, on
mesure seulement que N disques reçoivent exactement ce qu'on a lu une fois, et
à quel débit.

`cloner` lance un vrai clonage du port 1 vers les cibles, avec un affichage
texte, en attendant l'interface curses de la phase 4.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time

from . import devices, image, storage, sysexec
from .engine import backup, clone, fanout, sources
from .journal import Journal

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


def cloner(ports: list[int] | None, delai_blocage: float) -> int:
    """Clone le port 1 vers les cibles, en attendant l'interface de la phase 4.

    Source et cibles sont prises par leur rôle actuel, jamais par leur chemin.
    """
    disques = devices.inventaire()
    source = devices.source(disques)
    if source is None:
        print("Aucun disque dans le port 1.")
        return 1
    cibles = _cibles(disques, ports)
    if not cibles:
        return 1

    print(f"Source  port {source.port}  {source.description}  {_taille(source.taille)}"
          f"  s/n {source.serie}")
    _annoncer_cibles(cibles)

    with Journal("clonage") as journal:
        clonage = clone.Clonage(source, cibles, journal, delai_blocage=delai_blocage)
        _suivre(clonage)
        return _rapport(clonage, journal)


def images() -> int:
    """Les disques d'images, et les images complètes de chacun."""
    candidats = storage.candidats()
    if not candidats:
        print("Aucun disque USB de stockage monté.")
        return 1
    for candidat in candidats:
        print(f"{candidat.disque.description}  ({candidat.disque.chemin}, {candidat.racine}, "
              f"{candidat.fstype})  {_taille(candidat.libre)} libres")
        if candidat.refus:
            print(f"  refusé : {candidat.refus}")
            continue
        trouvees = image.lister(candidat.racine)
        if not trouvees:
            print("  aucune image")
        for img in trouvees:
            origine = img.origine
            print(f"  {img.nom:<40} {img.mode:<5} {_taille(img.taille_sur_disque):>11}"
                  f"   {origine.get('modele', '?')}, {_taille(int(origine.get('taille', 0)))}")
    return 0


def sauvegarder(etiquette: str, brut: bool, serie_stockage: str | None,
                delai_blocage: float) -> int:
    """Sauvegarde le disque du port 1 vers une image sur le disque USB."""
    source = devices.source(devices.inventaire())
    if source is None:
        print("Aucun disque dans le port 1.")
        return 1
    destination = _stockage(serie_stockage)
    if destination is None:
        return 1

    print(f"Source   port {source.port}  {source.description}  {_taille(source.taille)}"
          f"  s/n {source.serie}")
    print(f"Images   {destination.disque.description} ({destination.racine}), "
          f"{_taille(destination.libre)} libres")
    print(f"Mode     {'brut intégral — LENT' if brut else 'automatique'}")
    print()

    with Journal("sauvegarde") as journal:
        sauvegarde = backup.Sauvegarde(source, destination.racine, etiquette, journal,
                                       brut=brut, delai_blocage=delai_blocage)
        _suivre(sauvegarde)
        print()
        print(f"Durée totale : {sauvegarde.duree / 60:.1f} min")
        print(f"Verdict : {sauvegarde.etat.upper()}"
              + (f" — {sauvegarde.motif}" if sauvegarde.motif else ""))
        for avertissement in sauvegarde.avertissements:
            print(f"  ⚠ {avertissement}")
        if sauvegarde.etat == backup.REUSSIE:
            print(f"Image : {sauvegarde.dossier} ({_taille(image.lire(sauvegarde.dossier).taille_sur_disque)})")
        else:
            print(f"Dossier laissé incomplet, jamais proposé à la restauration : {sauvegarde.dossier}")
        print(f"Journal : {journal.dossier}")
    return 0 if sauvegarde.etat == backup.REUSSIE else 2


def restaurer(nom: str, ports: list[int] | None, sans_verification: bool,
              delai_blocage: float) -> int:
    """Restaure une image du disque USB vers les cibles."""
    trouvee = None
    for candidat in storage.candidats():
        if candidat.utilisable:
            trouvee = next((i for i in image.lister(candidat.racine) if i.nom == nom), trouvee)
    if trouvee is None:
        print(f"Aucune image complète « {nom} ». La liste : python3 -m clonegator images")
        return 1
    if sans_verification and trouvee.mode != image.MODE_BRUT:
        print("La vérification des empreintes ne se saute que pour une image brute (§8).")
        return 1

    cibles = _cibles(devices.inventaire(), ports)
    if not cibles:
        return 1
    print(f"Image   {trouvee.nom}  ({trouvee.mode}, {_taille(trouvee.taille_sur_disque)}), "
          f"taille requise {_taille(trouvee.taille_requise)}")
    _annoncer_cibles(cibles)

    with Journal("restauration") as journal:
        clonage = clone.Clonage(sources.SourceImage(trouvee, verifier=not sans_verification),
                                cibles, journal, delai_blocage=delai_blocage)
        _suivre(clonage)
        return _rapport(clonage, journal)


# ------------------------------------------------------------- affichage ---

def _cibles(disques, ports):
    cibles = [d for d in devices.cibles(disques) if ports is None or d.port in ports]
    if not cibles:
        print("Aucune cible retenue.")
    return cibles


def _annoncer_cibles(cibles) -> None:
    for cible in cibles:
        print(f"Cible   port {cible.port}  {cible.description}  {_taille(cible.taille)}"
              f"  s/n {cible.serie}")
    print()


def _stockage(serie: str | None):
    candidats = [c for c in storage.candidats() if c.utilisable]
    if serie:
        candidats = [c for c in candidats if c.disque.serie == serie]
    if len(candidats) == 1:
        return candidats[0]
    if not candidats:
        print("Aucun disque USB de stockage utilisable. La liste : python3 -m clonegator images")
    else:
        print("Plusieurs disques de stockage : préciser --stockage <numéro de série>.")
    return None


def _suivre(operation) -> None:
    """Lance l'opération dans un fil et affiche sa progression jusqu'à la fin."""
    fil = threading.Thread(target=operation.executer, name="operation")
    fil.start()
    derniere_etape = None
    try:
        while fil.is_alive():
            fil.join(timeout=2.0)
            if operation.etape != derniere_etape:
                if derniere_etape is not None:
                    print()
                print(f"— {operation.etape}")
                derniere_etape = operation.etape
            diffusion = operation.diffusion
            if diffusion is not None:
                _afficher_progression_clonage(diffusion)
    except KeyboardInterrupt:
        print("\nInterruption demandée…")
        operation.arreter()
        fil.join()


def _rapport(clonage, journal) -> int:
    print()
    print(f"Durée totale : {clonage.duree / 60:.1f} min")
    print()
    print(f"{'Cible':<8} {'Numéro de série':<18} Verdict")
    for cible in clonage.cibles:
        print(f"{cible.nom:<8} {cible.disque.serie:<18} {cible.etat.upper()}"
              + (f" — {cible.motif}" if cible.motif else ""))
        for avertissement in cible.avertissements:
            print(f"{'':<27}  ⚠ {avertissement}")
    print()
    print(f"Journal : {journal.dossier}")
    return 0 if all(c.etat == clone.REUSSIE for c in clonage.cibles) else 2


def _afficher_progression_clonage(diffusion: fanout.Diffusion) -> None:
    morceaux = [f"{_taille(diffusion.octets_lus)} lus"]
    for cible in diffusion.cibles:
        if cible.active:
            morceaux.append(f"{cible.nom} {cible.debit / 1e6:4.0f} Mo/s")
        else:
            morceaux.append(f"{cible.nom} {cible.etat}")
    print("\r  " + "  ".join(morceaux) + "   ", end="", flush=True)
