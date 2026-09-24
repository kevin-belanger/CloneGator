"""Point d'entrée en ligne de commande.

L'interface curses viendra en phase 4. D'ici là, ces sous-commandes servent au
développement et aux essais : elles montrent ce que le logiciel comprend du
matériel, sans rien écrire nulle part.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import VERSION, devices, essais

_ETIQUETTES = {
    devices.ROLE_SOURCE: "SOURCE",
    devices.ROLE_CIBLE: "CIBLE",
    devices.ROLE_STOCKAGE: "STOCKAGE",
    devices.ROLE_IGNORE: "ignoré",
}


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

    if disque.montee:
        morceaux.append("MONTÉ")

    return ", ".join(morceaux)


def cmd_inventaire(_args) -> int:
    disques = devices.inventaire()
    if not disques:
        print("Aucun disque détecté. lsblk est-il disponible ?", file=sys.stderr)
        return 1

    par_port = {
        disque.port: disque
        for disque in disques
        if disque.clonable and disque.port is not None
    }

    print(f"CloneGator {VERSION} — inventaire")
    print()

    ports = devices.ports_ata()
    if ports:
        for port in ports:
            role = (
                _ETIQUETTES[devices.ROLE_SOURCE]
                if port == devices.PORT_SOURCE
                else _ETIQUETTES[devices.ROLE_CIBLE]
            )
            disque = par_port.get(port)
            if disque:
                detail = (
                    f"{disque.description:<26} {taille_lisible(disque.taille):>10}"
                    f"  {_resume_contenu(disque)}"
                )
            else:
                detail = f"{'(vide)':<26}"
            print(f"  Port {port:<3} {role:<9} {detail}")
    else:
        print("  Aucun port ATA exposé par le noyau (machine sans contrôleur SATA ?)")

    print()
    for disque in devices.stockages(disques):
        detail = (
            f"{disque.description:<26} {taille_lisible(disque.taille):>10}"
            f"  {_resume_contenu(disque)}"
        )
        print(f"  {'USB':<8} {_ETIQUETTES[devices.ROLE_STOCKAGE]:<9} {detail}")

    ignores = devices.par_role(disques, devices.ROLE_IGNORE)
    if ignores:
        print()
        print("  Hors périmètre :")
        for disque in ignores:
            print(
                f"    {disque.chemin:<16} bus {disque.bus:<8}"
                f" {taille_lisible(disque.taille):>10}"
            )

    return 0


def cmd_disques(_args) -> int:
    """Vue brute, une ligne par disque, pratique pour vérifier un branchement."""
    for disque in devices.inventaire():
        port = f"port {disque.port}" if disque.port is not None else "—"
        print(
            f"{disque.chemin:<14} {disque.bus:<8} {port:<8}"
            f" {disque.role:<9} {taille_lisible(disque.taille):>10}"
            f"  secteur {disque.secteur_logique}"
            f"  {disque.description}"
            f"  s/n {disque.serie or '?'}"
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
    return essais.essai_diffusion(
        volume=int(args.volume * essais.Gio),
        ports=args.ports,
        delai_blocage=args.delai,
    )


def cmd_version(_args) -> int:
    print(VERSION)
    return 0


def construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        prog="clonegator",
        description="Station de duplication et de sauvegarde de disques.",
    )
    analyseur.add_argument(
        "-v", "--verbeux",
        action="store_true",
        help="journalise chaque commande système lancée",
    )

    sous = analyseur.add_subparsers(dest="commande", required=True)

    sous.add_parser(
        "inventaire", help="affiche le tableau des ports"
    ).set_defaults(fonction=cmd_inventaire)

    sous.add_parser(
        "disques", help="vue brute, une ligne par disque et par partition"
    ).set_defaults(fonction=cmd_disques)

    essai = sous.add_parser(
        "essai-diffusion",
        help="diffuse le début du port 1 vers les cibles et vérifie (ÉCRASE LES CIBLES)",
    )
    essai.add_argument("--volume", type=float, default=8, help="en Gio (défaut : 8)")
    essai.add_argument(
        "--ports", type=int, nargs="+", help="ports cibles (défaut : toutes les cibles)"
    )
    essai.add_argument(
        "--delai", type=float, default=60, help="délai de blocage en secondes (défaut : 60)"
    )
    essai.set_defaults(fonction=cmd_essai_diffusion)

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
