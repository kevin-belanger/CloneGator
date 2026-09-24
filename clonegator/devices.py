"""Inventaire des disques.

Le reste du logiciel ne connaît que les objets `Disque` produits ici. Il ne lit
jamais `lsblk` ni `/dev/disk/by-path` lui-même, et ne manipule jamais un
`/dev/sdX` qu'il aurait deviné.

**C'est ici que vit P2**, la règle porteuse de l'analyse : un disque dont le bus
est USB n'obtient jamais le rôle source ni le rôle cible. Le système de la
station et le stockage d'images sont sur USB, donc rien ne peut les écraser, et
aucune vérification supplémentaire n'est nécessaire ailleurs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import sysexec

_log = logging.getLogger("clonegator.devices")

# Le port SATA qui porte toujours le disque à copier (§3 de l'analyse).
PORT_SOURCE = 1

ROLE_SOURCE = "source"
ROLE_CIBLE = "cible"
ROLE_STOCKAGE = "stockage"
ROLE_IGNORE = "ignore"

# Bus sur lesquels un clonage est permis. L'USB en est absent, et c'est tout
# l'enjeu : ne rien ajouter ici sans relire P2.
BUS_CLONABLES = frozenset({"sata", "ata"})

_BY_PATH = "/dev/disk/by-path"
_ATA_PORTS = "/sys/class/ata_port"

# Le noyau nomme les liens de deux façons selon sa version :
#   pci-0000:00:17.0-ata-1        (ancienne)
#   pci-0000:00:17.0-ata-1.0      (depuis l'ajout du numéro de lien)
# `clonesrv` n'acceptait que la première : sur un noyau récent, il ne détectait
# plus aucun disque et affichait six baies vides. Les deux sont acceptées ici.
_LIEN_ATA = re.compile(r"-ata-(\d+)(?:\.\d+)?$")
_NOM_PORT_ATA = re.compile(r"ata(\d+)$")
_NUMERO_FINAL = re.compile(r"(\d+)$")

_COLONNES = (
    "PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,PTTYPE,LOG-SEC,FSTYPE,MOUNTPOINT,FSUSED,UUID,LABEL"
)


@dataclass
class Partition:
    numero: int
    chemin: str
    taille: int
    fstype: str | None = None
    point_montage: str | None = None
    utilise: int | None = None
    uuid: str | None = None  # celui du système de fichiers, pas de la partition
    etiquette: str | None = None

    @property
    def montee(self) -> bool:
        return bool(self.point_montage)


@dataclass
class Disque:
    chemin: str
    bus: str
    port: int | None
    modele: str
    serie: str
    taille: int
    secteur_logique: int
    table: str | None
    partitions: list[Partition] = field(default_factory=list)

    @property
    def role(self) -> str:
        """Ce que ce disque a le droit de devenir.

        Un disque USB est un support de stockage, jamais une source ni une
        cible : c'est P2, et elle est appliquée ici et nulle part ailleurs.
        """
        if self.bus == "usb":
            return ROLE_STOCKAGE
        if self.bus in BUS_CLONABLES and self.port is not None:
            return ROLE_SOURCE if self.port == PORT_SOURCE else ROLE_CIBLE
        return ROLE_IGNORE

    @property
    def clonable(self) -> bool:
        return self.role in (ROLE_SOURCE, ROLE_CIBLE)

    @property
    def montee(self) -> bool:
        return any(partition.montee for partition in self.partitions)

    @property
    def utilise(self) -> int | None:
        """Somme de l'espace occupé, quand les partitions savent le dire.

        `lsblk` ne renseigne `FSUSED` que pour ce qu'il peut sonder — souvent
        rien tant que la partition n'est pas montée. La mesure fiable viendra
        de partclone en phase 2 ; d'ici là, None veut dire « inconnu », pas
        « zéro », et l'affichage doit le distinguer.
        """
        connus = [p.utilise for p in self.partitions if p.utilise is not None]
        return sum(connus) if connus else None

    @property
    def description(self) -> str:
        return self.modele or self.chemin


def ports_ata() -> list[int]:
    """Tous les ports ATA de la carte mère, occupés ou non.

    `clonesrv` bouclait sur « 1 2 3 4 5 6 » en dur. Le noyau expose la liste
    réelle, ce qui permet à la fois de s'adapter à la machine et d'afficher une
    baie vide — un lien `by-path` n'existe que si un disque est branché, il ne
    peut donc pas servir à ça.
    """
    numeros = []
    for nom in sysexec.lister(_ATA_PORTS):
        trouve = _NOM_PORT_ATA.fullmatch(nom)
        if trouve:
            numeros.append(int(trouve.group(1)))
    return sorted(numeros)


def ports_occupes() -> dict[str, int]:
    """Associe le chemin réel d'un disque au numéro de port ATA qui le porte."""
    ports: dict[str, int] = {}
    for nom in sysexec.lister(_BY_PATH):
        trouve = _LIEN_ATA.search(nom)
        if not trouve:
            continue  # écarte aussi les entrées « -partN », qui ne finissent pas là
        reel = sysexec.chemin_reel(f"{_BY_PATH}/{nom}")
        if reel:
            ports[reel] = int(trouve.group(1))
    return ports


def inventaire() -> list[Disque]:
    """Tous les disques que la machine expose, décrits une bonne fois."""
    brut = sysexec.executer_json(
        # --tree est indispensable : dès qu'on choisit ses colonnes sans NAME,
        # lsblk rend une liste plate, sans « children », et aucune partition
        # n'est rattachée à son disque.
        ["lsblk", "--json", "--tree", "--bytes", "-o", _COLONNES]
    )
    if not brut:
        _log.error("lsblk n'a rien renvoyé d'exploitable")
        return []

    ports = ports_occupes()
    disques = []

    for noeud in brut.get("blockdevices", []):
        if noeud.get("type") != "disk":
            continue
        disques.append(_disque_depuis(noeud, ports))

    disques.sort(key=_ordre_affichage)
    return disques


def decrire(chemin: str) -> Disque | None:
    """Un seul disque, relu à l'instant — une cible dont on vient d'écrire la
    table, par exemple.

    Accepte aussi un disque en boucle du banc d'essai, que l'inventaire écarte :
    il n'a pas de port, donc pas de rôle, mais le moteur de copie ne travaille
    que sur des chemins et peut s'en servir.
    """
    brut = sysexec.executer_json(
        ["lsblk", "--json", "--tree", "--bytes", "-o", _COLONNES, chemin]
    )
    if not brut or not brut.get("blockdevices"):
        return None
    noeud = brut["blockdevices"][0]
    if noeud.get("type") not in ("disk", "loop"):
        return None
    return _disque_depuis(noeud, ports_occupes())


def par_role(disques: list[Disque], role: str) -> list[Disque]:
    return [disque for disque in disques if disque.role == role]


def source(disques: list[Disque]) -> Disque | None:
    trouves = par_role(disques, ROLE_SOURCE)
    return trouves[0] if trouves else None


def cibles(disques: list[Disque]) -> list[Disque]:
    return par_role(disques, ROLE_CIBLE)


def stockages(disques: list[Disque]) -> list[Disque]:
    """Disques USB pouvant accueillir des images.

    Celui qui porte le système de la station en fait partie : rien n'interdit
    d'y ranger des sauvegardes (§7.4), et il est de toute façon protégé du
    clonage par P2.
    """
    return par_role(disques, ROLE_STOCKAGE)


def proteger(disque: Disque) -> bool:
    """Passe le disque et chacune de ses partitions en lecture seule noyau.

    C'est P1 : le disque du port 1 n'est jamais écrit. Le drapeau porte sur
    chaque périphérique séparément — protéger /dev/sdX laisserait /dev/sdX3
    inscriptible —, d'où la boucle sur les partitions. Revient à False si un
    seul d'entre eux n'a pas pu être protégé.

    Le noyau n'applique pas ce drapeau à l'ouverture mais à chaque écriture :
    un `open` en écriture réussit, le `write` échoue (EPERM). Vérifié sur les
    baies en réécrivant à l'identique un bloc du disque et de chaque partition.
    """
    return _poser_lecture_seule(disque, True)


def liberer(disque: Disque) -> bool:
    """Rend au disque son état normal, en fin d'opération (§13)."""
    return _poser_lecture_seule(disque, False)


def _poser_lecture_seule(disque: Disque, lecture_seule: bool) -> bool:
    option = "--setro" if lecture_seule else "--setrw"
    chemins = [disque.chemin] + [partition.chemin for partition in disque.partitions]
    ok = True
    for chemin in chemins:
        if not sysexec.executer(["blockdev", option, chemin]).ok:
            _log.error("blockdev %s a échoué sur %s", option, chemin)
            ok = False
    return ok


def _disque_depuis(noeud: dict, ports: dict[str, int]) -> Disque:
    chemin = noeud.get("path") or ""
    reel = sysexec.chemin_reel(chemin) if chemin else None

    return Disque(
        chemin=chemin,
        bus=(noeud.get("tran") or "inconnu").lower(),
        port=ports.get(reel),
        modele=_propre(noeud.get("model")),
        serie=_propre(noeud.get("serial")),
        taille=_entier(noeud.get("size")),
        secteur_logique=_entier(noeud.get("log-sec")) or 512,
        table=noeud.get("pttype"),
        partitions=_partitions_depuis(noeud, chemin),
    )


def _partitions_depuis(noeud: dict, chemin_disque: str) -> list[Partition]:
    partitions = []
    for enfant in noeud.get("children", []) or []:
        if enfant.get("type") != "part":
            continue

        chemin = enfant.get("path") or ""
        numero = _numero_partition(chemin, chemin_disque)
        if numero is None:
            _log.warning("numéro de partition indéchiffrable : %s", chemin)
            continue

        partitions.append(
            Partition(
                numero=numero,
                chemin=chemin,
                taille=_entier(enfant.get("size")),
                fstype=enfant.get("fstype"),
                point_montage=enfant.get("mountpoint"),
                utilise=_entier_ou_none(enfant.get("fsused")),
                uuid=enfant.get("uuid"),
                etiquette=enfant.get("label"),
            )
        )

    # Triées par numéro réel, jamais renumérotées. Une source dont les
    # partitions sont 1, 2, 3 et 5 reste une source à quatre partitions
    # numérotées 1, 2, 3 et 5 — l'hypothèse de contiguïté est ce qui faisait
    # abandonner `clonesrv` après avoir déjà effacé les cibles.
    partitions.sort(key=lambda partition: partition.numero)
    return partitions


def _numero_partition(chemin: str, chemin_disque: str) -> int | None:
    reste = chemin[len(chemin_disque):] if chemin.startswith(chemin_disque) else chemin
    trouve = _NUMERO_FINAL.search(reste)
    return int(trouve.group(1)) if trouve else None


def _ordre_affichage(disque: Disque) -> tuple:
    rang = {ROLE_SOURCE: 0, ROLE_CIBLE: 1, ROLE_STOCKAGE: 2, ROLE_IGNORE: 3}
    return (rang[disque.role], disque.port or 0, disque.chemin)


def _propre(valeur) -> str:
    return str(valeur).strip() if valeur else ""


def _entier(valeur) -> int:
    return _entier_ou_none(valeur) or 0


def _entier_ou_none(valeur) -> int | None:
    if valeur is None or valeur == "":
        return None
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None
