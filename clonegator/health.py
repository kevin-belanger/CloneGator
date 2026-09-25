"""État de santé des disques, d'après SMART (§12 de l'analyse).

Une seule commande par disque, `smartctl -H -A --json`, résumée en trois mots :

  - **défaillant** : le disque se déclare lui-même en échec (verdict SMART, ou
    alerte critique d'un NVMe). Écarté comme cible par défaut (`devices`) ;
  - **usure** : secteurs réalloués, en attente ou illisibles, erreurs
    signalées, ou moins de 20 % de durée de vie restante ;
  - **ok**.

Chaque fabricant nomme ses attributs à sa façon : on en lit plusieurs, et on
ne conclut qu'à partir de ce que le disque expose. Un disque qui ne répond pas
à SMART — derrière un adaptateur USB qui ne le transmet pas — reste « inconnu »,
sans être écarté.

Les résultats sont gardés une minute : l'accueil du mode station se rafraîchit
toutes les deux secondes, pas les disques.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import sysexec

OK = "ok"
USURE = "usure"
DEFAILLANT = "défaillant"
INCONNU = "inconnu"

_DUREE_CACHE = 60.0

# Attributs ATA dont une valeur brute non nulle signale des secteurs abîmés.
_SECTEURS = {5: "secteurs réalloués", 187: "erreurs non corrigées",
             197: "secteurs en attente", 198: "secteurs illisibles"}
# Attributs ATA d'usure d'un SSD, en valeur normalisée : 100 = neuf.
_USURE_SSD = (231, 202, 177, 233)


@dataclass
class Sante:
    niveau: str
    detail: str = ""

    def __str__(self) -> str:
        return f"SMART : {self.niveau}" + (f" ({self.detail})" if self.detail else "")


_cache: dict[tuple[str, str], tuple[float, Sante]] = {}


def etat(disque) -> Sante:
    cle = (disque.chemin, disque.serie)
    connu = _cache.get(cle)
    if connu and time.monotonic() - connu[0] < _DUREE_CACHE:
        return connu[1]
    resultat = sysexec.executer(["smartctl", "-H", "-A", "--json", disque.chemin],
                                echec_prevu=True)
    try:
        sante = resumer(json.loads(resultat.sortie))
    except ValueError:
        sante = Sante(INCONNU)
    _cache[cle] = (time.monotonic(), sante)
    return sante


def resumer(rapport: dict) -> Sante:
    """Le rapport JSON de smartctl, résumé en un mot et un détail."""
    statut = rapport.get("smart_status", {})
    nvme = rapport.get("nvme_smart_health_information_log")
    if statut.get("passed") is False:
        return Sante(DEFAILLANT, "le disque se déclare en échec")
    if nvme and nvme.get("critical_warning"):
        return Sante(DEFAILLANT, "alerte critique NVMe")

    signes = []
    for attribut in rapport.get("ata_smart_attributes", {}).get("table", []):
        ident = attribut.get("id")
        brut = attribut.get("raw", {}).get("value", 0)
        if ident in _SECTEURS and brut:
            signes.append(f"{brut} {_SECTEURS[ident]}")
        elif ident in _USURE_SSD and attribut.get("value", 100) <= 20:
            signes.append(f"{attribut.get('value')} % de vie restante")
    if nvme:
        if nvme.get("percentage_used", 0) >= 80:
            signes.append(f"{nvme['percentage_used']} % de vie consommée")
        if nvme.get("media_errors"):
            signes.append(f"{nvme['media_errors']} erreurs de support")

    if signes:
        return Sante(USURE, ", ".join(signes))
    if "passed" not in statut:
        return Sante(INCONNU)
    return Sante(OK)
