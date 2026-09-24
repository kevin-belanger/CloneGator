"""Sauvegarde d'un disque vers une image (§7 de l'analyse).

Une sauvegarde, c'est un disque et une image. La lecture est celle du clonage —
même source, mêmes moteurs par partition, même protection P1 — ; seule change la
destination : un fichier compressé par partition au lieu de N disques.

Pour chaque partition, `partclone` (ou la partition elle-même, pour une copie
brute) alimente directement `zstd`, sans passer par Python. Le moteur de
diffusion reçoit la sortie de `zstd`, l'écrit dans le fichier et en calcule
l'empreinte au passage : pas de relecture, et la même surveillance de blocage
qu'un clonage.

Tout échec laisse un dossier sans `clonegator.json` : une image incomplète,
jamais proposée à la restauration, et jamais effacée (§7.5).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time

from .. import filesystems, image, sysexec
from ..devices import Disque
from ..journal import Journal
from . import fanout
from .sources import ErreurSource, Flux, Plan, SourceDisque

_log = logging.getLogger("clonegator.backup")

REUSSIE = fanout.REUSSIE
ECHEC = fanout.ECHEC
INTERROMPUE = fanout.INTERROMPUE

DELAI_FIN_PROCESSUS = 300.0

# Niveau 3 sur tous les processeurs : près de 2 Go/s sur la station de
# développement, dix fois la vitesse de lecture du maître. La compression ne
# ralentit jamais la sauvegarde.
_ZSTD = ["zstd", "-q", "-3", "-T0", "-c"]


class ErreurSauvegarde(Exception):
    pass


class Sauvegarde:
    """Une sauvegarde, du premier contrôle au `clonegator.json`.

    `etape` et `diffusion` peuvent être lus depuis un autre fil pour afficher
    la progression, et `arreter()` y être appelé pour interrompre.
    """

    def __init__(
        self,
        disque: Disque,
        racine_stockage: str,
        etiquette: str,
        journal: Journal,
        *,
        brut: bool = False,
        delai_blocage: float = 60.0,
    ):
        self.source = SourceDisque(disque, brut=brut)
        self.disque = disque
        self.etiquette = etiquette
        self.journal = journal
        self.delai_blocage = delai_blocage
        # Une seule heure pour le nom du dossier et la date de l'image : celle
        # du début de la sauvegarde.
        self._heure = time.time()
        self.dossier = os.path.join(racine_stockage, image.RACINE,
                                    image.nom_dossier(etiquette, self._heure))

        self.etat = fanout.EN_COURS
        self.motif = ""
        self.avertissements: list[str] = []
        self.etape = "préparation"
        self.diffusion: fanout.Diffusion | None = None
        self.duree = 0.0

        self._arret = threading.Event()
        self._processus: list[sysexec.Processus] = []
        self._empreintes: dict[str, str] = {}

    def arreter(self) -> None:
        self._arret.set()
        if self.diffusion is not None:
            self.diffusion.arreter()
        for processus in list(self._processus):
            processus.tuer()

    def executer(self) -> str:
        debut = time.monotonic()
        _log.info("sauvegarde de %s vers %s", self.source.description, self.dossier)
        try:
            self.source.preparer()
            self._derouler()
            self.etat = REUSSIE
            self.etape = "terminé"
        except (ErreurSource, ErreurSauvegarde, OSError) as erreur:
            self.etat = INTERROMPUE if self._arret.is_set() else ECHEC
            self.motif = str(erreur)
        except BaseException as erreur:
            self.etat = INTERROMPUE if isinstance(erreur, KeyboardInterrupt) else ECHEC
            self.motif = "sauvegarde interrompue" if self.etat == INTERROMPUE else f"erreur interne : {erreur!r}"
            if self.etat == ECHEC:
                _log.exception("erreur interne")
            raise
        finally:
            for processus in list(self._processus):
                processus.tuer()
            self.source.liberer()
            self.duree = time.monotonic() - debut
            if self.etat == REUSSIE:
                _log.info("image complète : %s", self.dossier)
            else:
                _log.warning("sauvegarde %s — %s ; dossier laissé incomplet : %s",
                             self.etat, self.motif, self.dossier)
        return self.etat

    # ---------------------------------------------------------- déroulement ---

    def _derouler(self) -> None:
        os.makedirs(os.path.dirname(self.dossier), exist_ok=True)
        os.mkdir(self.dossier)  # jamais d'écrasement d'une image existante

        if self.source.brut:
            self.etape = "copie intégrale du disque"
            self._compresser(self.source.ouvrir_disque_brut(self.journal),
                             image.DISQUE_BRUT, "disque entier")
            partitions = []
            taille_requise = self.disque.taille
        else:
            table = self.source.table
            self.etape = "tête du disque et table"
            self._ecrire_petit(image.TABLE, table.description.encode())
            flux = self.source.ouvrir_tete()
            try:
                tete = os.pread(flux.fd, table.debut_premiere_partition * table.secteur, 0)
            finally:
                os.close(flux.fd)
            self._ecrire_petit(image.TETE, tete)

            partitions = []
            for rang, plan in enumerate(self.source.plans, start=1):
                self.etape = (f"partition {plan.entree.numero} ({rang} sur "
                              f"{len(self.source.plans)}) — {plan.choix}")
                partitions.append(self._sauvegarder_partition(plan))
                if self._arret.is_set():
                    raise ErreurSauvegarde("sauvegarde interrompue")
            taille_requise = table.taille_requise

        self.etape = "finalisation"
        meta = {
            "mode": image.MODE_BRUT if self.source.brut else image.MODE_AUTO,
            "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(self._heure)),
            "etiquette": self.etiquette,
            "disque_origine": {
                "modele": self.disque.description,
                "serie": self.disque.serie,
                "taille": self.disque.taille,
                "emplacement": (f"port {self.disque.port}" if self.disque.port is not None
                                else self.disque.chemin),
            },
            "secteur": self.source.secteur,
            "taille_requise": taille_requise,
            "partitions": partitions,
        }
        image.ecrire_lisezmoi(self.dossier, meta)
        self._empreintes[image.LISEZMOI] = image.empreinte_fichier(
            os.path.join(self.dossier, image.LISEZMOI))
        image.ecrire_empreintes(self.dossier, self._empreintes)
        shutil.copyfile(self.journal.chemin, os.path.join(self.dossier, image.JOURNAL))
        image.ecrire_metadonnees(self.dossier, meta)  # en dernier : l'image existe

    def _sauvegarder_partition(self, plan: Plan) -> dict:
        numero = plan.entree.numero
        moteur = plan.choix.moteur
        decrite = {
            "numero": numero,
            "moteur": moteur,
            "systeme": plan.fstype,
            "taille": plan.entree.taille * self.source.secteur,
        }
        if plan.choix.avertissement:
            self.avertissements.append(f"partition {numero} : {plan.choix.raison}")

        if moteur == filesystems.AUCUN:
            return decrite
        if moteur == filesystems.SWAP:
            uuid, etiquette = self.source.swap(plan)
            decrite.update(uuid=uuid, etiquette=etiquette)
            return decrite

        fichier = image.fichier_partition(numero, moteur, plan.fstype)
        if moteur == filesystems.BRUT:
            flux = self.source.flux_brut(plan, self.journal)
        else:
            flux = self.source.flux_partclone(plan, self.journal)
            decrite["programme"] = plan.choix.programme
        self._compresser(flux, fichier, f"partition {numero}")
        decrite["fichier"] = fichier

        if plan.fstype == "ntfs" and moteur == filesystems.PARTCLONE:
            secours = self.source.secours_ntfs(plan)
            if secours is not None:
                position, contenu = secours
                nom = image.fichier_secours_ntfs(numero)
                self._ecrire_petit(nom, contenu)
                decrite["secours"] = {"fichier": nom, "position": position}
        return decrite

    # --------------------------------------------------------- compression ---

    def _compresser(self, flux: Flux, fichier: str, quoi: str) -> None:
        """Le flux, compressé par zstd, dans `fichier` ; son empreinte est retenue."""
        compresseur = self._suivre(sysexec.Processus(
            _ZSTD,
            entree_fd=flux.fd,
            flux_sortant=True,
            erreurs=self.journal.fichier(f"{fichier}_zstd.err"),
        ))
        # zstd a reçu sa propre copie de l'entrée : fermer la nôtre, pour que
        # sa mort éventuelle se propage au programme en amont.
        if flux.processus:
            lecteur = self._suivre(flux.processus)
            lecteur.ceder_sortie()
        else:
            lecteur = None
        if flux.a_fermer:
            os.close(flux.fd)

        chemin = os.path.join(self.dossier, fichier)
        fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
        try:
            diffusion = fanout.Diffusion(
                compresseur.sortie,
                [fanout.Destination(fichier, fd)],
                delai_blocage=self.delai_blocage,
                empreinte=True,
            )
            self.diffusion = diffusion
            try:
                diffusion.executer()
            finally:
                self.diffusion = None
                if not diffusion.fin_de_flux:
                    compresseur.tuer()
                    if lecteur:
                        lecteur.tuer()
        finally:
            os.close(fd)

        fin_compresseur = self._attendre(compresseur)
        fin_lecteur = self._attendre(lecteur) if lecteur else None
        suivi = diffusion.cibles[0]

        if fin_lecteur is not None and not fin_lecteur.ok:
            raise ErreurSauvegarde(f"{quoi} : lecture impossible — {_derniere(fin_lecteur.erreur)}")
        if not fin_compresseur.ok:
            raise ErreurSauvegarde(f"{quoi} : zstd a échoué — {_derniere(fin_compresseur.erreur)}")
        if suivi.etat != REUSSIE:
            raise ErreurSauvegarde(f"{quoi} : écriture de {fichier} — {suivi.motif}")
        self._empreintes[fichier] = diffusion.empreinte_source
        _log.info("%s : %s, %d octets", quoi, fichier, suivi.octets)

    def _ecrire_petit(self, fichier: str, contenu: bytes) -> None:
        chemin = os.path.join(self.dossier, fichier)
        with open(chemin, "xb") as sortie:
            sortie.write(contenu)
            sortie.flush()
            os.fsync(sortie.fileno())
        self._empreintes[fichier] = image.empreinte_fichier(chemin)

    def _suivre(self, processus: sysexec.Processus) -> sysexec.Processus:
        self._processus.append(processus)
        return processus

    def _attendre(self, processus: sysexec.Processus) -> sysexec.Resultat:
        resultat = processus.attendre(DELAI_FIN_PROCESSUS)
        if processus in self._processus:
            self._processus.remove(processus)
        return resultat


def _derniere(texte: str) -> str:
    lignes = [ligne.strip() for ligne in (texte or "").splitlines() if ligne.strip()]
    return lignes[-1] if lignes else "sans message"
