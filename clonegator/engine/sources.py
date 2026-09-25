"""Ce que le moteur de clonage copie : un disque, ou une image.

Une restauration n'est qu'un clonage dont la source est une image (§8 de
l'analyse). Les deux sources offrent donc les mêmes opérations au moteur, qui ne
sait pas laquelle il utilise :

| Opération           | Source disque                      | Source image                  |
|---------------------|------------------------------------|-------------------------------|
| préparer            | lecture seule noyau (P1)           | vérification des empreintes   |
| table               | `sfdisk` sur le disque             | `disque.sfdisk`               |
| moteur par partition| choisi selon le contenu (§6.2)     | lu dans `clonegator.json`     |
| flux partclone      | `partclone -c` sur la partition    | `zstd -d` du fichier          |
| copie brute         | la partition                       | `zstd -d` du fichier          |
| secours NTFS        | lu sur la partition                | fichier `pN.ntfs-secours`     |

Tout flux est rendu sous forme d'un descripteur à lire et, le cas échéant, du
programme qui l'alimente : le moteur le surveille et juge de son code de
retour.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .. import devices, filesystems, image, layout, sysexec
from ..devices import Disque
from ..journal import Journal

_log = logging.getLogger("clonegator.sources")


class ErreurSource(Exception):
    """La source ne peut pas servir : aucune cible ne sera touchée."""


@dataclass
class Plan:
    """Ce qui sera fait d'une partition, décidé avant toute écriture."""

    entree: layout.Entree
    choix: filesystems.Choix
    fstype: str | None


@dataclass
class Flux:
    """Des octets à lire, et le programme qui les produit, s'il y en a un."""

    fd: int
    processus: sysexec.Processus | None = None
    a_fermer: bool = False  # le descripteur nous appartient : le fermer après usage


class SourceDisque:
    """Un disque, lu partition par partition ou en entier (§6.3)."""

    def __init__(self, disque: Disque, *, brut: bool = False):
        self.disque = disque
        self.brut = brut
        self.table: layout.Table | None = None
        self.plans: list[Plan] = []
        self._partitions = {p.numero: p for p in disque.partitions}
        self._protege = False

    @property
    def description(self) -> str:
        return f"{self.disque.libelle}, {self.disque.description}, s/n {self.disque.serie}"

    # Ce que l'écran de progression affiche pendant `preparer()`.
    etape_preparation = "protection de la source en lecture seule"

    @property
    def secteur(self) -> int:
        return self.table.secteur if self.table else self.disque.secteur_logique

    @property
    def taille_requise(self) -> int:
        return self.disque.taille if self.brut else self.table.taille_requise

    def preparer(self) -> None:
        refus = devices.refus_comme_source(self.disque)
        if refus:
            raise ErreurSource(f"{self.disque.libelle} : {refus}")
        if not devices.proteger(self.disque):
            raise ErreurSource("le disque source n'a pas pu être mis en lecture seule")
        self._protege = True
        if self.brut:
            return
        try:
            self.table = layout.lire(self.disque.chemin)
        except layout.ErreurTable as erreur:
            raise ErreurSource(str(erreur)) from erreur
        self.plans = self._planifier()

    def liberer(self) -> None:
        if self._protege:
            devices.liberer(self.disque)
            self._protege = False

    def _planifier(self) -> list[Plan]:
        plans = []
        for entree in self.table.entrees:
            partition = self._partitions.get(entree.numero)
            if partition is None:
                # La table la déclare, le noyau ne l'expose pas : on ne peut
                # pas la lire, donc on ne peut pas la copier.
                raise ErreurSource(
                    f"la partition {entree.numero} de la source n'est pas visible par le système"
                )
            choix = filesystems.choisir(partition, entree.etendue)
            _log.info("partition %d (%s, %d octets) : %s", entree.numero,
                      partition.fstype or "aucun système de fichiers",
                      entree.taille * self.table.secteur, choix)
            plans.append(Plan(entree, choix, partition.fstype))
        return plans

    def ouvrir_tete(self) -> Flux:
        return Flux(sysexec.ouvrir(self.disque.chemin), a_fermer=True)

    def ouvrir_disque_brut(self, journal: Journal) -> Flux:
        return Flux(sysexec.ouvrir(self.disque.chemin), a_fermer=True)

    def flux_partclone(self, plan: Plan, journal: Journal) -> Flux:
        numero = plan.entree.numero
        processus = sysexec.Processus(
            [plan.choix.programme, "-c", "-s", self._partitions[numero].chemin, "-o", "-",
             "-L", journal.fichier(f"p{numero}_source_partclone.log")],
            flux_sortant=True,
            erreurs=journal.fichier(f"p{numero}_source.err"),
        )
        return Flux(processus.sortie, processus)

    def flux_brut(self, plan: Plan, journal: Journal) -> Flux:
        return Flux(sysexec.ouvrir(self._partitions[plan.entree.numero].chemin), a_fermer=True)

    def swap(self, plan: Plan) -> tuple[str | None, str | None]:
        partition = self._partitions[plan.entree.numero]
        return partition.uuid, partition.etiquette

    def secours_ntfs(self, plan: Plan) -> tuple[int, bytes] | None:
        return filesystems.secours_ntfs(self._partitions[plan.entree.numero].chemin)

    def volume(self, plan: Plan) -> int:
        """Octets que la copie de cette partition lira : sert à estimer la
        progression d'une restauration future."""
        return filesystems.volume_a_copier(self._partitions[plan.entree.numero], plan.choix)


class SourceImage:
    """Une image du §7.2. Ses empreintes sont vérifiées avant toute écriture."""

    def __init__(self, img: image.Image, *, verifier: bool = True):
        self.image = img
        self.verifier = verifier
        self.brut = img.mode == image.MODE_BRUT
        self.table: layout.Table | None = None
        self.plans: list[Plan] = []
        self._partitions = {int(p["numero"]): p for p in img.partitions}

    @property
    def description(self) -> str:
        origine = self.image.origine
        return (f"sauvegarde « {self.image.etiquette} » du {self.image.meta.get('date', '?')}, "
                f"d'un {origine.get('modele', '?')} s/n {origine.get('serie', '?')}")

    @property
    def etape_preparation(self) -> str:
        if not self.verifier:
            return "lecture de la sauvegarde"
        return (f"vérification de la sauvegarde (relecture de "
                f"{self.image.taille_sur_disque / 1e9:.1f} Go)".replace(".", ","))

    @property
    def secteur(self) -> int:
        return int(self.image.meta.get("secteur", 512))

    @property
    def taille_requise(self) -> int:
        return self.image.taille_requise

    def preparer(self) -> None:
        if self.verifier:
            _log.info("vérification des empreintes de %s", self.image.nom)
            motif = image.verifier_empreintes(self.image)
            if motif:
                raise ErreurSource(motif)
        if self.brut:
            return
        try:
            with open(self.image.chemin(image.TABLE), encoding="utf-8") as fichier:
                self.table = layout.depuis_description(fichier.read())
        except (OSError, layout.ErreurTable) as erreur:
            raise ErreurSource(f"table de l'image illisible : {erreur}") from erreur

        for entree in self.table.entrees:
            partition = self._partitions.get(entree.numero)
            if partition is None:
                raise ErreurSource(f"la partition {entree.numero} manque dans {image.METADONNEES}")
            choix = filesystems.Choix(partition["moteur"], partition.get("programme"))
            self.plans.append(Plan(entree, choix, partition.get("systeme")))
            _log.info("partition %d : %s", entree.numero, choix)

    def liberer(self) -> None:
        pass  # rien n'a été touché

    def ouvrir_tete(self) -> Flux:
        return Flux(os.open(self.image.chemin(image.TETE), os.O_RDONLY | os.O_CLOEXEC), a_fermer=True)

    def ouvrir_disque_brut(self, journal: Journal) -> Flux:
        return self._decompresser(image.DISQUE_BRUT, "disque", journal)

    def flux_partclone(self, plan: Plan, journal: Journal) -> Flux:
        numero = plan.entree.numero
        return self._decompresser(self._partitions[numero]["fichier"], f"p{numero}", journal)

    def flux_brut(self, plan: Plan, journal: Journal) -> Flux:
        return self.flux_partclone(plan, journal)

    def swap(self, plan: Plan) -> tuple[str | None, str | None]:
        partition = self._partitions[plan.entree.numero]
        return partition.get("uuid"), partition.get("etiquette")

    def secours_ntfs(self, plan: Plan) -> tuple[int, bytes] | None:
        secours = self._partitions[plan.entree.numero].get("secours")
        if not secours:
            return None
        with open(self.image.chemin(secours["fichier"]), "rb") as fichier:
            return int(secours["position"]), fichier.read()

    def _decompresser(self, fichier: str, nom: str, journal: Journal) -> Flux:
        processus = sysexec.Processus(
            ["zstd", "-q", "-d", "-c", self.image.chemin(fichier)],
            flux_sortant=True,
            erreurs=journal.fichier(f"{nom}_zstd.err"),
        )
        return Flux(processus.sortie, processus)
