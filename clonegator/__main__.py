"""Point d'entrée en ligne de commande.

Les sous-commandes servent au développement et aux essais, en attendant
l'interface curses : `inventaire` et `disques` montrent ce que le logiciel
comprend du matériel sans rien écrire ; les autres lancent les vraies
opérations, sur des disques désignés par leur emplacement.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import VERSION, devices, essais


def taille_lisible(octets: int | None) -> str:
    """Taille en unités décimales, comme les fabricants et `lsblk --bytes`."""
    if octets is None:
        return "?"
    if octets < 1000:
        return f"{octets} o"

    valeur = float(octets)
    for unite in ("ko", "Mo", "Go", "To", "Po"):
        valeur /= 1000.0
        if valeur < 1000.0:
            return f"{valeur:.1f} {unite}".replace(".", ",")
    return f"{valeur:.1f} Eo".replace(".", ",")


def _resume_contenu(disque: devices.Disque) -> str:
    if not disque.table and not disque.partitions:
        return "aucune table de partitions"

    morceaux = [(disque.table or "table inconnue").upper()]
    nombre = len(disque.partitions)
    morceaux.append(f"{nombre} part." if nombre else "0 part.")

    numeros = [partition.numero for partition in disque.partitions]
    if numeros and numeros != list(range(1, nombre + 1)):
        # Cas que `clonesrv` ne savait pas traiter : on le rend visible.
        morceaux.append("numéros " + ", ".join(str(n) for n in numeros))

    if disque.utilise is not None:
        morceaux.append(f"{taille_lisible(disque.utilise)} utilisés")

    return ", ".join(morceaux)


def cmd_inventaire(_args) -> int:
    """Les emplacements de la machine, baies vides comprises, et ce que chaque
    disque peut devenir."""
    disques = devices.inventaire()
    par_cle = {d.emplacement.cle: d for d in disques if d.emplacement}

    print(f"CloneGator {VERSION} — inventaire")
    print()

    lignes = [(e, par_cle.get(e.cle)) for e in devices.emplacements_sata()]
    deja = {e.cle for e, _ in lignes}
    lignes += [(d.emplacement, d) for d in disques
               if d.emplacement and d.emplacement.cle not in deja]

    for emplacement, disque in lignes:
        if disque is None:
            print(f"  {emplacement.nom:<14} (vide)")
            continue
        refus = devices.refus_comme_cible(disque)
        print(f"  {disque.libelle:<14} {disque.description:<22} {taille_lisible(disque.taille):>10}"
              f"  {_resume_contenu(disque):<34} {refus or 'disponible'}")
    return 0


def cmd_disques(_args) -> int:
    """Vue brute, une ligne par disque, pratique pour vérifier un branchement."""
    for disque in devices.inventaire():
        cle = disque.emplacement.cle if disque.emplacement else "—"
        print(
            f"{disque.libelle:<14} {disque.bus:<5} {cle:<24}"
            f" {taille_lisible(disque.taille):>10}  secteur {disque.secteur_logique}"
            f"  {disque.description}  s/n {disque.serie or '?'}"
        )
        for partition in disque.partitions:
            montage = f" montée sur {partition.point_montage}" if partition.montee else ""
            print(
                f"    {partition.numero:>2}  {partition.chemin:<16}"
                f" {taille_lisible(partition.taille):>10}"
                f"  {partition.fstype or 'brut'}{montage}"
            )
    return 0


def cmd_essai_diffusion(args) -> int:
    return essais.essai_diffusion(int(args.volume * essais.Gio), args.source, args.cibles,
                                  args.delai)


def cmd_cloner(args) -> int:
    return essais.cloner(args.source, args.cibles, args.delai)


def cmd_images(_args) -> int:
    return essais.images()


def cmd_sauvegarder(args) -> int:
    return essais.sauvegarder(args.nom, args.source, args.brut, args.stockage, args.delai)


def cmd_restaurer(args) -> int:
    return essais.restaurer(args.sauvegarde, args.cibles, args.sans_verification, args.delai)


def cmd_version(_args) -> int:
    print(VERSION)
    return 0


def construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        prog="clonegator",
        description="Duplication et sauvegarde de disques.",
    )
    analyseur.add_argument(
        "-v", "--verbeux",
        action="store_true",
        help="journalise chaque commande système lancée",
    )

    sous = analyseur.add_subparsers(dest="commande", required=True)

    sous.add_parser(
        "inventaire", help="les emplacements et ce que chaque disque peut devenir"
    ).set_defaults(fonction=cmd_inventaire)

    sous.add_parser(
        "disques", help="vue brute, une ligne par disque et par partition"
    ).set_defaults(fonction=cmd_disques)

    def delai(commande):
        commande.add_argument("--delai", type=float, default=60,
                              help="délai de blocage en secondes (défaut : 60)")

    essai = sous.add_parser(
        "essai-diffusion",
        help="diffuse le début de la source vers les cibles et vérifie (ÉCRASE LES CIBLES)",
    )
    essai.add_argument("--source", required=True, help="emplacement, ex. SATA1")
    essai.add_argument("--cibles", required=True, nargs="+", help="emplacements, ex. SATA2 SATA3")
    essai.add_argument("--volume", type=float, default=8, help="en Gio (défaut : 8)")
    delai(essai)
    essai.set_defaults(fonction=cmd_essai_diffusion)

    cloner = sous.add_parser("cloner", help="clone un disque vers d'autres (ÉCRASE LES CIBLES)")
    cloner.add_argument("--source", required=True, help="emplacement, ex. SATA1")
    cloner.add_argument("--cibles", required=True, nargs="+", help="emplacements, ex. SATA2 SATA3")
    delai(cloner)
    cloner.set_defaults(fonction=cmd_cloner)

    sous.add_parser(
        "images", help="les disques de sauvegardes et leurs sauvegardes"
    ).set_defaults(fonction=cmd_images)

    sauvegarde = sous.add_parser("sauvegarder", help="sauvegarde un disque vers le disque USB")
    sauvegarde.add_argument("nom", help="nom de la sauvegarde, ex. Win11-labo")
    sauvegarde.add_argument("--source", required=True, help="emplacement, ex. SATA1")
    sauvegarde.add_argument("--brut", action="store_true",
                            help="copie brute intégrale du disque (lent, §6.3)")
    sauvegarde.add_argument("--stockage",
                            help="numéro de série du disque de sauvegardes, s'il y en a plusieurs")
    delai(sauvegarde)
    sauvegarde.set_defaults(fonction=cmd_sauvegarder)

    restauration = sous.add_parser(
        "restaurer", help="restaure une sauvegarde vers des disques (ÉCRASE LES CIBLES)"
    )
    restauration.add_argument("sauvegarde",
                              help="nom du dossier de la sauvegarde (voir « images »)")
    restauration.add_argument("--cibles", required=True, nargs="+",
                              help="emplacements, ex. SATA2 SATA3")
    restauration.add_argument("--sans-verification", action="store_true",
                              help="sauvegarde brute seulement : ne pas relire les empreintes")
    delai(restauration)
    restauration.set_defaults(fonction=cmd_restaurer)

    sous.add_parser(
        "version", help="affiche la version"
    ).set_defaults(fonction=cmd_version)

    return analyseur


def main(argv: list[str] | None = None) -> int:
    args = construire_analyseur().parse_args(argv)

    # Le niveau porte sur l'écran seulement : le journal d'une opération reçoit
    # tout, quel que soit ce réglage.
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if args.verbeux else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s : %(message)s"))
    logging.getLogger().addHandler(console)
    logging.getLogger().setLevel(logging.DEBUG)

    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())
