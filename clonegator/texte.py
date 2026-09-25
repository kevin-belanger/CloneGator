"""Mises en forme partagées par l'interface et les sous-commandes."""

from __future__ import annotations


def taille(octets: int | None) -> str:
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


def duree(secondes: float) -> str:
    """« 45 s », « 2 min 10 s », « 1 h 05 »."""
    secondes = int(round(secondes))
    if secondes < 60:
        return f"{secondes} s"
    minutes, secondes = divmod(secondes, 60)
    if minutes < 60:
        return f"{minutes} min {secondes:02d} s" if minutes < 10 else f"{minutes} min"
    heures, minutes = divmod(minutes, 60)
    return f"{heures} h {minutes:02d}"


def debit(octets_par_seconde: float) -> str:
    return f"{octets_par_seconde / 1e6:.0f} Mo/s"


def contenu(disque) -> str:
    """« GPT, 5 partitions : vfat, ntfs », de quoi reconnaître un disque d'un coup d'œil."""
    if not disque.table and not disque.partitions:
        return "vierge ou sans table de partitions"
    morceaux = [(disque.table or "table inconnue").upper()]
    nombre = len(disque.partitions)
    types = []
    for partition in disque.partitions:
        if partition.fstype and partition.fstype not in types:
            types.append(partition.fstype)
    partitions = f"{nombre} partition{'s' if nombre > 1 else ''}"
    if types:
        partitions += " : " + ", ".join(types)
    morceaux.append(partitions)

    numeros = [p.numero for p in disque.partitions]
    if numeros and numeros != list(range(1, nombre + 1)):
        # Cas que `clonesrv` ne savait pas traiter : on le rend visible.
        morceaux.append("numéros " + ", ".join(map(str, numeros)))
    if disque.utilise is not None:
        morceaux.append(f"{taille(disque.utilise)} utilisés")
    return ", ".join(morceaux)
