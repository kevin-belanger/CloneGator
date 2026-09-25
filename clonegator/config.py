"""Réglages enregistrés (§15 de l'analyse), dans `/etc/clonegator/`.

Ce qui survit à un redémarrage : le mode courant, le réglage du mode station
avec son lancement automatique, et la connexion au partage réseau — **sans son
mot de passe**, jamais enregistré (§7.4).

Un fichier absent ou illisible donne les réglages par défaut : mode libre, rien
de mémorisé. Il ne doit jamais empêcher CloneGator de démarrer.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field

_log = logging.getLogger("clonegator.config")

FICHIER = os.environ.get("CLONEGATOR_CONFIG", "/etc/clonegator/clonegator.json")

MODE_LIBRE = "libre"
MODE_STATION = "station"


@dataclass
class ReglageStation:
    source: str  # clé d'emplacement (§3.1), stable d'un démarrage à l'autre
    cibles: list[str] = field(default_factory=list)
    lancement_auto: bool = False


@dataclass
class ConnexionReseau:
    hote: str = ""
    partage: str = ""
    utilisateur: str = ""

    @property
    def renseignee(self) -> bool:
        return bool(self.hote and self.partage)

    @property
    def unc(self) -> str:
        return f"\\\\{self.hote}\\{self.partage}"


@dataclass
class Reglages:
    mode: str = MODE_LIBRE
    station: ReglageStation | None = None
    reseau: ConnexionReseau = field(default_factory=ConnexionReseau)


def lire() -> Reglages:
    try:
        with open(FICHIER, encoding="utf-8") as fichier:
            brut = json.load(fichier)
    except FileNotFoundError:
        return Reglages()
    except (OSError, ValueError) as erreur:
        _log.warning("%s illisible, réglages par défaut : %s", FICHIER, erreur)
        return Reglages()

    try:
        station = brut.get("station")
        return Reglages(
            mode=brut.get("mode", MODE_LIBRE) if brut.get("mode") in (MODE_LIBRE, MODE_STATION)
            else MODE_LIBRE,
            station=ReglageStation(
                source=station["source"],
                cibles=list(station.get("cibles", [])),
                lancement_auto=bool(station.get("lancement_auto", False)),
            ) if station else None,
            reseau=ConnexionReseau(**{
                cle: str(valeur) for cle, valeur in (brut.get("reseau") or {}).items()
                if cle in ("hote", "partage", "utilisateur")
            }),
        )
    except (AttributeError, KeyError, TypeError) as erreur:
        _log.warning("%s incohérent, réglages par défaut : %s", FICHIER, erreur)
        return Reglages()


def ecrire(reglages: Reglages) -> None:
    """Écrit les réglages d'un seul coup : un fichier à moitié écrit ne doit
    jamais être relu au démarrage suivant."""
    os.makedirs(os.path.dirname(FICHIER), exist_ok=True)
    temporaire = FICHIER + ".tmp"
    with open(temporaire, "w", encoding="utf-8") as fichier:
        json.dump(asdict(reglages), fichier, ensure_ascii=False, indent=2)
        fichier.write("\n")
        fichier.flush()
        os.fsync(fichier.fileno())
    os.replace(temporaire, FICHIER)
