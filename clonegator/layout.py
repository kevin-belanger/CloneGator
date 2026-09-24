"""Tables de partitions : lecture, numéros réels, reproduction (§6.2).

La table est lue avec `sfdisk --json` pour l'exploiter, et reproduite à partir
de `sfdisk --dump`, qui la décrit entièrement : étiquette, identifiant du disque,
et pour chaque partition son numéro, son début, sa taille, son type, son GUID,
son nom et ses attributs. Tout est reproduit à l'identique, GUID compris : le
chargeur de Windows désigne ses partitions par eux, une cible aux GUID changés
ne démarrerait pas.

Deux choses ne sont pas reprises telles quelles :

  - la ligne `last-lba` d'une GPT, pour que sfdisk place l'en-tête de secours à
    la fin de la cible, quelle que soit sa taille ;
  - la ligne `device`, propre au disque source.

Les numéros de partition viennent du nom de chaque ligne (`/dev/sdf5` → 5) :
une source numérotée 1, 2, 3, 5 donne une cible numérotée 1, 2, 3, 5.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import sysexec

_log = logging.getLogger("clonegator.layout")

# Types MBR des partitions étendues : des conteneurs, décrits par la table
# elle-même, qu'on ne copie jamais comme des données.
TYPES_ETENDUS = frozenset({"5", "f", "85"})

# Place que réserve une GPT en fin de disque pour son en-tête de secours :
# 32 secteurs d'entrées et 1 secteur d'en-tête.
SECTEURS_GPT_SECOURS = 33

_NUMERO_FINAL = re.compile(r"(\d+)$")


@dataclass
class Entree:
    """Une partition telle que la table la décrit."""

    numero: int
    debut: int  # en secteurs
    taille: int  # en secteurs
    type: str
    uuid: str | None = None
    nom: str | None = None
    attributs: str | None = None

    @property
    def fin(self) -> int:
        """Premier secteur après la partition."""
        return self.debut + self.taille

    @property
    def etendue(self) -> bool:
        return self.type.lower() in TYPES_ETENDUS


@dataclass
class Table:
    etiquette: str  # "gpt" ou "dos"
    identifiant: str | None
    secteur: int
    entrees: list[Entree] = field(default_factory=list)
    description: str = ""  # sortie de `sfdisk --dump`, pour la reproduction

    @property
    def gpt(self) -> bool:
        return self.etiquette == "gpt"

    def entree(self, numero: int) -> Entree | None:
        return next((e for e in self.entrees if e.numero == numero), None)

    @property
    def debut_premiere_partition(self) -> int:
        """En secteurs. Tout ce qui précède est la tête du disque : code
        d'amorçage, table elle-même, et l'espace qu'un chargeur peut y loger."""
        return min((e.debut for e in self.entrees), default=0)

    @property
    def taille_requise(self) -> int:
        """Octets qu'une cible doit offrir au moins pour recevoir cette table."""
        fin = max((e.fin for e in self.entrees), default=0)
        if self.gpt:
            fin += SECTEURS_GPT_SECOURS
        return fin * self.secteur


class ErreurTable(Exception):
    pass


def lire(chemin: str) -> Table:
    """La table d'un disque. Lève ErreurTable s'il n'en a pas de lisible."""
    brut = sysexec.executer_json(["sfdisk", "--json", chemin])
    if not brut or "partitiontable" not in brut:
        raise ErreurTable(f"aucune table de partitions lisible sur {chemin}")
    table_json = brut["partitiontable"]

    description = sysexec.executer(["sfdisk", "--dump", chemin])
    if not description.ok:
        raise ErreurTable(f"sfdisk --dump a échoué sur {chemin} : {description.erreur.strip()}")

    table = Table(
        etiquette=table_json.get("label", ""),
        identifiant=table_json.get("id"),
        secteur=int(table_json.get("sectorsize", 512)),
        description=description.sortie,
    )
    for partition in table_json.get("partitions", []):
        numero = _numero(partition.get("node", ""))
        if numero is None:
            raise ErreurTable(f"numéro de partition indéchiffrable : {partition.get('node')}")
        table.entrees.append(
            Entree(
                numero=numero,
                debut=int(partition["start"]),
                taille=int(partition["size"]),
                type=str(partition.get("type", "")),
                uuid=partition.get("uuid"),
                nom=partition.get("name"),
                attributs=partition.get("attrs"),
            )
        )
    table.entrees.sort(key=lambda entree: entree.numero)

    if table.etiquette not in ("gpt", "dos"):
        raise ErreurTable(f"table de type « {table.etiquette} » non gérée")
    return table


def description_pour_cible(table: Table) -> str:
    """Le `sfdisk --dump` de la source, débarrassé de ce qui lui est propre."""
    lignes = []
    for ligne in table.description.splitlines():
        cle = ligne.split(":", 1)[0].strip()
        if cle in ("device", "last-lba"):
            continue
        lignes.append(ligne)
    return "\n".join(lignes) + "\n"


def reproduire(table: Table, chemin_cible: str) -> sysexec.Resultat:
    """Écrit la table sur la cible, en effaçant d'abord toute trace de l'ancienne.

    `--wipe always` efface les signatures du disque (ancienne GPT, en-tête de
    secours en fin de disque compris) ; `--wipe-partitions always` celles qui
    traîneraient dans les nouvelles partitions — sans quoi le reste d'un ancien
    ext4 pourrait coexister avec le nouveau système de fichiers et le rendre
    méconnaissable.
    """
    return sysexec.executer(
        [
            "sfdisk",
            "--wipe", "always",
            "--wipe-partitions", "always",
            chemin_cible,
        ],
        entree=description_pour_cible(table),
        delai=120,
    )


def comparer(attendue: Table, obtenue: Table) -> list[str]:
    """Différences entre deux tables, en français ; vide si elles concordent.

    L'identifiant du disque et, par partition, numéro, début, taille, type et
    GUID : ce qui fait qu'un système démarre. La fin du disque, elle, diffère
    légitimement d'une cible plus grande.
    """
    ecarts = []
    if attendue.etiquette != obtenue.etiquette:
        ecarts.append(f"table {obtenue.etiquette} au lieu de {attendue.etiquette}")
    if (attendue.identifiant or "").lower() != (obtenue.identifiant or "").lower():
        ecarts.append("identifiant du disque différent")

    numeros_attendus = [e.numero for e in attendue.entrees]
    numeros_obtenus = [e.numero for e in obtenue.entrees]
    if numeros_attendus != numeros_obtenus:
        ecarts.append(f"partitions {numeros_obtenus} au lieu de {numeros_attendus}")

    for a in attendue.entrees:
        o = obtenue.entree(a.numero)
        if o is None:
            continue
        for libelle, va, vo in (
            ("début", a.debut, o.debut),
            ("taille", a.taille, o.taille),
            ("type", a.type.lower(), o.type.lower()),
            ("GUID", (a.uuid or "").lower(), (o.uuid or "").lower()),
        ):
            if va != vo:
                ecarts.append(f"partition {a.numero} : {libelle} différent")
    return ecarts


def _numero(noeud: str) -> int | None:
    trouve = _NUMERO_FINAL.search(noeud)
    return int(trouve.group(1)) if trouve else None
