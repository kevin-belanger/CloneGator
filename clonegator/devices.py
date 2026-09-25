"""Inventaire des disques, leurs emplacements, et ce qu'ils peuvent devenir.

Le reste du logiciel ne connaît que les objets `Disque` produits ici. Il ne lit
jamais `lsblk` ni `/sys` lui-même, et ne manipule jamais un `/dev/sdX` qu'il
aurait deviné.

**C'est ici, et seulement ici, que se décide ce qu'un disque peut devenir**
(§2 et §3 de l'analyse) :

  - son emplacement, `SATA2`, `NVMe1`, `USB3` (§3.1), stable d'un démarrage à
    l'autre, contrairement à `/dev/sdX` ;
  - les deux filets de P2 : un disque utilisé par le système n'est ni source ni
    cible, un disque qui contient des sauvegardes n'est jamais une cible ;
  - ce qui peut entrer dans un réglage de station (§3.3) et ce qui peut
    accueillir des sauvegardes (§3.4).

Le moteur de copie ne regarde jamais rien de tout ça : il copie ce qu'on lui
donne. Ne pas dupliquer ces règles ailleurs, ne pas en ajouter par-dessus.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from . import image, montage, sysexec

_log = logging.getLogger("clonegator.devices")

BUS_SATA = "sata"
BUS_NVME = "nvme"
BUS_USB = "usb"
BUS_AUTRE = "autre"

REFUS_SYSTEME = "utilisé par le système"
REFUS_SAUVEGARDES = "contient des sauvegardes CloneGator"

_COLONNES = (
    "PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,PTTYPE,LOG-SEC,FSTYPE,MOUNTPOINT,FSUSED,UUID,LABEL"
)

_PCI = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]")
_PORT_ATA = re.compile(r"ata\d+")
_RACINE_USB = re.compile(r"usb(\d+)")
_APPAREIL_USB = re.compile(r"(\d+)-([\d.]+)")
_NVME = re.compile(r"nvme(\d+)n(\d+)")
_NUMERO_FINAL = re.compile(r"(\d+)$")


# ----------------------------------------------------------- emplacements ---

@dataclass(frozen=True)
class Emplacement:
    """L'endroit où un disque est branché (§3.1)."""

    bus: str
    cle: str  # identité stable, celle qu'on enregistre : « sata:0000:00:17.0:2 »
    nom: str  # ce qu'on affiche : « SATA2 »

    @property
    def interne(self) -> bool:
        return self.bus in (BUS_SATA, BUS_NVME)


class _Topologie:
    """Les contrôleurs de la machine, relevés une fois par inventaire.

    La numérotation suit l'ordre des adresses PCI des contrôleurs, puis celui
    des ports : deux contrôleurs SATA de six ports donnent SATA1 à SATA12,
    jamais deux « SATA1 ».
    """

    def __init__(self):
        ports = []
        for nom in sysexec.lister("/sys/class/ata_port"):
            reel = sysexec.chemin_reel(f"/sys/class/ata_port/{nom}") or ""
            numero = sysexec.lire(f"/sys/class/ata_port/{nom}/port_no")
            if numero and numero.isdigit():
                ports.append((_pci_avant(reel, nom), int(numero), nom))
        ports.sort()
        self.sata = [
            Emplacement(BUS_SATA, f"sata:{pci}:{numero}", f"SATA{rang}")
            for rang, (pci, numero, _) in enumerate(ports, start=1)
        ]
        self._sata_par_port = {nom: e for (_, _, nom), e in zip(ports, self.sata)}

        self._nvme = sorted({
            _pci_avant(sysexec.chemin_reel(f"/sys/class/nvme/{nom}") or "", "nvme")
            for nom in sysexec.lister("/sys/class/nvme")
        })
        self._usb = sorted({
            _pci_avant(sysexec.chemin_reel(f"/sys/bus/usb/devices/{nom}") or "", nom)
            for nom in sysexec.lister("/sys/bus/usb/devices")
            if _RACINE_USB.fullmatch(nom)
        })

    def emplacement(self, nom_noyau: str) -> Emplacement:
        reel = sysexec.chemin_reel(f"/sys/block/{nom_noyau}") or ""
        parties = reel.split("/")

        for partie in parties:
            if _PORT_ATA.fullmatch(partie) and partie in self._sata_par_port:
                return self._sata_par_port[partie]

        nvme = _NVME.fullmatch(nom_noyau)
        if nvme:
            controleur = sysexec.chemin_reel(f"/sys/class/nvme/nvme{nvme.group(1)}") or ""
            pci = _pci_avant(controleur, "nvme")
            if pci in self._nvme:
                rang = self._nvme.index(pci) + 1
                espace = int(nvme.group(2))
                nom = f"NVMe{rang}" + (f"-{espace}" if espace != 1 else "")
                return Emplacement(BUS_NVME, f"nvme:{pci}:{espace}", nom)

        for i, partie in enumerate(parties):
            racine = _RACINE_USB.fullmatch(partie)
            if racine:
                return self._emplacement_usb(parties, i, int(racine.group(1)))

        return Emplacement(BUS_AUTRE, f"autre:{nom_noyau}", nom_noyau)

    def _emplacement_usb(self, parties: list[str], i: int, bus: int) -> Emplacement:
        """Un connecteur USB, quelle que soit la vitesse de l'appareil branché.

        Un connecteur USB 3 est vu deux fois par le noyau : un port sur le
        concentrateur racine USB 3, un autre sur le concentrateur USB 2. Le
        noyau les apparie (`peer`) ; on nomme le connecteur d'après son port
        USB 2, pour qu'un appareil lent ou rapide y reçoive le même nom.
        """
        pci = parties[i - 1] if i else ""
        chaine = [p for p in parties[i + 1:] if _APPAREIL_USB.fullmatch(p)]
        ports = chaine[-1].split("-", 1)[1].split(".") if chaine else ["0"]
        racine = ports[0]

        vitesse = sysexec.lire(f"/sys/bus/usb/devices/usb{bus}/speed") or "0"
        if vitesse.isdigit() and int(vitesse) >= 5000:
            jumeau = sysexec.chemin_reel(
                f"/sys/bus/usb/devices/usb{bus}/{bus}-0:1.0/usb{bus}-port{racine}/peer"
            )
            port_usb2 = _NUMERO_FINAL.search(os.path.basename(jumeau or ""))
            if port_usb2:
                racine = port_usb2.group(1)
            else:
                racine = f"s{racine}"  # port USB 3 sans jumeau : rare, gardé distinct

        chemin = ".".join([racine, *ports[1:]])
        prefixe = ""
        if len(self._usb) > 1 and pci in self._usb:
            prefixe = f"{self._usb.index(pci) + 1}-"
        return Emplacement(BUS_USB, f"usb:{pci}:{chemin}", f"USB{prefixe}{chemin}")


def _pci_avant(chemin_reel: str, repere: str) -> str:
    """L'adresse PCI du contrôleur : la dernière avant `repere` dans le chemin."""
    parties = chemin_reel.split("/")
    fin = parties.index(repere) if repere in parties else len(parties)
    for partie in reversed(parties[:fin]):
        if _PCI.fullmatch(partie):
            return partie
    return ""


# --------------------------------------------------------------- disques ---

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
    emplacement: Emplacement | None
    modele: str
    serie: str
    taille: int
    secteur_logique: int
    table: str | None
    partitions: list[Partition] = field(default_factory=list)

    @property
    def nom_noyau(self) -> str:
        return os.path.basename(self.chemin)

    @property
    def libelle(self) -> str:
        """« SATA2 (sdb) » : l'emplacement d'abord, le nom système à titre indicatif (P3)."""
        if self.emplacement is None or self.emplacement.nom == self.nom_noyau:
            return self.nom_noyau
        return f"{self.emplacement.nom} ({self.nom_noyau})"

    @property
    def montee(self) -> bool:
        return any(partition.montee for partition in self.partitions)

    @property
    def utilise(self) -> int | None:
        """Somme de l'espace occupé, quand les partitions savent le dire.

        `lsblk` ne renseigne `FSUSED` que pour ce qu'il peut sonder — souvent
        rien tant que la partition n'est pas montée. None veut dire « inconnu »,
        pas « zéro », et l'affichage doit le distinguer.
        """
        connus = [p.utilise for p in self.partitions if p.utilise is not None]
        return sum(connus) if connus else None

    @property
    def description(self) -> str:
        return self.modele or self.chemin


def inventaire() -> list[Disque]:
    """Tous les disques que la machine expose, décrits une bonne fois, dans
    l'ordre des emplacements."""
    brut = sysexec.executer_json(
        # --tree est indispensable : dès qu'on choisit ses colonnes sans NAME,
        # lsblk rend une liste plate, sans « children », et aucune partition
        # n'est rattachée à son disque.
        ["lsblk", "--json", "--tree", "--bytes", "-o", _COLONNES]
    )
    if not brut:
        _log.error("lsblk n'a rien renvoyé d'exploitable")
        return []

    topologie = _Topologie()
    disques = [
        _disque_depuis(noeud, topologie)
        for noeud in brut.get("blockdevices", [])
        if noeud.get("type") == "disk" and _vrai_disque(noeud)
    ]
    disques.sort(key=_ordre)
    return disques


def emplacements_sata() -> list[Emplacement]:
    """Tous les ports SATA de la machine, occupés ou non — pour afficher une
    baie vide, qu'aucun disque ne désigne."""
    return _Topologie().sata


def decrire(chemin: str) -> Disque | None:
    """Un seul disque, relu à l'instant — une cible dont on vient d'écrire la
    table, par exemple.

    Accepte aussi un disque en boucle du banc d'essai, que l'inventaire écarte :
    le moteur de copie ne travaille que sur des chemins et peut s'en servir.
    """
    brut = sysexec.executer_json(
        ["lsblk", "--json", "--tree", "--bytes", "-o", _COLONNES, chemin]
    )
    if not brut or not brut.get("blockdevices"):
        return None
    noeud = brut["blockdevices"][0]
    if noeud.get("type") not in ("disk", "loop"):
        return None
    return _disque_depuis(noeud, _Topologie())


def montages(chemin: str) -> list[tuple[str, str]]:
    """Les systèmes de fichiers montés d'un disque, à toute profondeur :
    (point de montage, type). Celui d'un volume LVM, par exemple, est un
    petit-enfant du disque, pas une de ses partitions."""
    brut = sysexec.executer_json(
        ["lsblk", "--json", "--tree", "-o", "PATH,FSTYPE,MOUNTPOINT", chemin]
    )
    trouves: list[tuple[str, str]] = []

    def parcourir(noeud: dict) -> None:
        if noeud.get("mountpoint") and not noeud["mountpoint"].startswith("["):  # [SWAP]
            trouves.append((noeud["mountpoint"], noeud.get("fstype") or ""))
        for enfant in noeud.get("children", []) or []:
            parcourir(enfant)

    for noeud in (brut or {}).get("blockdevices", []):
        parcourir(noeud)
    return trouves


# ------------------------------------------------ ce qu'un disque peut devenir ---

def utilise_par_le_systeme(disque: Disque) -> bool:
    """Premier filet de P2 : le noyau refuse de l'ouvrir en exclusivité."""
    try:
        return sysexec.tenu_par_le_systeme(disque.chemin)
    except OSError as erreur:
        _log.warning("%s : test d'exclusivité impossible (%s)", disque.chemin, erreur)
        return True  # dans le doute, on ne le propose pas


# Résultats des sondes, par partition et système de fichiers : une sonde monte
# la partition, on ne la refait pas à chaque rafraîchissement de l'écran.
_sondes: dict[tuple, bool] = {}


def contient_sauvegardes(disque: Disque) -> bool:
    """Second filet de P2 : un dossier `CloneGator/` à la racine d'une de ses
    partitions, même non montée — elle est alors sondée, sans rien y écrire."""
    for partition in disque.partitions:
        if partition.point_montage:
            if os.path.isdir(os.path.join(partition.point_montage, image.RACINE)):
                return True
            continue
        cle = (partition.chemin, partition.uuid, partition.fstype, partition.taille)
        if cle not in _sondes:
            with montage.sonde(partition.chemin, partition.fstype) as point:
                _sondes[cle] = bool(point) and os.path.isdir(os.path.join(point, image.RACINE))
        if _sondes[cle]:
            return True
    return False


def refus_comme_source(disque: Disque) -> str:
    """Motif pour lequel ce disque ne peut pas être source, ou chaîne vide."""
    return REFUS_SYSTEME if utilise_par_le_systeme(disque) else ""


def refus_comme_cible(disque: Disque) -> str:
    """Motif pour lequel ce disque ne peut pas être cible, ou chaîne vide."""
    if utilise_par_le_systeme(disque):
        return REFUS_SYSTEME
    if contient_sauvegardes(disque):
        return REFUS_SAUVEGARDES
    return ""


def admis_en_station(emplacement: Emplacement) -> bool:
    """§3.3 : seuls les emplacements internes entrent dans un réglage de station.
    Tout disque d'un emplacement cible y est effacé sans qu'on l'ait choisi ; un
    connecteur USB reçoit des clés, des claviers, le disque de sauvegardes."""
    return emplacement.interne


def peut_stocker(disque: Disque) -> bool:
    """§3.4 : les sauvegardes vivent sur un disque USB, jamais sur un disque interne."""
    return disque.bus == BUS_USB


# ------------------------------------------------------------------- P1 ---

def proteger(disque: Disque) -> bool:
    """Passe le disque et chacune de ses partitions en lecture seule noyau.

    C'est P1 : la source d'une opération n'est jamais écrite. Le drapeau porte
    sur chaque périphérique séparément — protéger /dev/sdX laisserait /dev/sdX3
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


# ---------------------------------------------------------------- interne ---

def _vrai_disque(noeud: dict) -> bool:
    """Écarte ce que lsblk classe « disk » sans en être un pour nous : la
    mémoire compressée (zram), un lecteur de cartes vide."""
    nom = os.path.basename(noeud.get("path") or "")
    return not nom.startswith("zram") and _entier(noeud.get("size")) > 0


def _disque_depuis(noeud: dict, topologie: _Topologie) -> Disque:
    chemin = noeud.get("path") or ""
    nom = os.path.basename(chemin)
    return Disque(
        chemin=chemin,
        bus=(noeud.get("tran") or ("nvme" if nom.startswith("nvme") else "inconnu")).lower(),
        emplacement=topologie.emplacement(nom) if nom else None,
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


def _ordre(disque: Disque) -> tuple:
    """SATA, puis NVMe, puis USB, puis le reste ; dans l'ordre des numéros."""
    rang = {BUS_SATA: 0, BUS_NVME: 1, BUS_USB: 2}
    emplacement = disque.emplacement
    if emplacement is None:
        return (9, 0, disque.chemin)
    chiffres = [int(n) for n in re.findall(r"\d+", emplacement.nom)]
    return (rang.get(emplacement.bus, 3), chiffres, disque.chemin)


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
