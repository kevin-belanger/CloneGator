"""Clonage d'une source vers N disques (§6 et §8 de l'analyse).

La source est un disque, ou une image : une restauration n'est qu'un clonage
dont la source est une image (voir `sources.py`). Déroulement, dans cet ordre,
et c'est l'ordre qui compte :

  1. la source est préparée : un disque passe en lecture seule noyau (P1)
     jusqu'à la fin, une image voit ses empreintes vérifiées ;
  2. toutes les cibles sont validées **avant qu'une seule ne soit touchée**
     (§6.5) — une cible refusée est écartée, les autres continuent ;
  3. chaque cible reçoit la tête du disque source (code d'amorçage), puis sa
     table de partitions, reproduite à l'identique ;
  4. chaque partition est copiée vers toutes les cibles à la fois, la source
     n'étant lue qu'une fois, avec le moteur choisi pour elle (§6.2) ;
  5. chaque cible passe la vérification légère (§11).

Une source brute (§6.3) est copiée d'un bloc, sans étapes 3 à 5.

Une cible qui échoue à une étape est retirée des suivantes ; les autres
continuent. Chaque cible reçoit son propre verdict, jamais un verdict global.

Ce module ne regarde jamais le rôle d'un disque : choisir la source et les
cibles revient à l'appelant.
"""

from __future__ import annotations

import errno
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from .. import devices, filesystems, layout, sysexec, verify
from ..devices import Disque
from ..journal import Journal
from . import fanout
from .sources import ErreurSource, Flux, Plan, SourceDisque, SourceImage

_log = logging.getLogger("clonegator.clone")

EN_ATTENTE = fanout.EN_ATTENTE
EN_COURS = fanout.EN_COURS
REUSSIE = fanout.REUSSIE
ECHEC = fanout.ECHEC
BLOQUEE = fanout.BLOQUEE
INTERROMPUE = fanout.INTERROMPUE
ECARTEE = "écartée"

# Une fois son flux terminé, un partclone de restauration vide encore son
# tampon et synchronise le disque. Au-delà, il est tenu pour figé.
DELAI_FIN_PROCESSUS = 300.0


@dataclass
class Cible:
    """Une cible et son verdict, lisibles pendant le clonage depuis un autre fil."""

    disque: Disque
    etat: str = EN_ATTENTE
    motif: str = ""
    avertissements: list[str] = field(default_factory=list)
    partitions: dict[int, str] = field(default_factory=dict)  # numéro → chemin sur la cible

    @property
    def nom(self) -> str:
        if self.disque.emplacement is not None:
            return self.disque.emplacement.nom
        return self.disque.chemin

    @property
    def active(self) -> bool:
        return self.etat == EN_COURS

    def conclure(self, etat: str, motif: str = "") -> None:
        if self.etat not in (EN_ATTENTE, EN_COURS):
            return
        self.etat = etat
        self.motif = motif
        if etat == REUSSIE:
            _log.info("%s : %s", self.nom, etat)
        else:
            _log.warning("%s : %s — %s", self.nom, etat, motif)


class Clonage:
    """Un clonage ou une restauration, du premier contrôle au dernier verdict.

    `executer()` est bloquant. `etape` et `diffusion` (la copie en cours)
    peuvent être lus depuis un autre fil pour afficher la progression, et
    `arreter()` y être appelé pour interrompre.
    """

    def __init__(
        self,
        source: Disque | SourceDisque | SourceImage,
        cibles: list[Disque],
        journal: Journal,
        *,
        delai_blocage: float = 60.0,
    ):
        if isinstance(source, Disque):
            source = SourceDisque(source)
        self.source = source
        self.cibles = [Cible(disque) for disque in cibles]
        self.journal = journal
        self.delai_blocage = delai_blocage

        self.etape = "préparation"
        self.diffusion: fanout.Diffusion | None = None
        self.table: layout.Table | None = None
        self.plans: list[Plan] = []
        self.duree = 0.0

        self._arret = threading.Event()
        self._processus: list[sysexec.Processus] = []

    def arreter(self) -> None:
        self._arret.set()
        if self.diffusion is not None:
            self.diffusion.arreter()
        for processus in list(self._processus):
            processus.tuer()

    @property
    def actives(self) -> list[Cible]:
        return [cible for cible in self.cibles if cible.active]

    # ------------------------------------------------------------ ensemble ---

    def executer(self) -> list[Cible]:
        debut = time.monotonic()
        _log.info("source : %s", self.source.description)
        for cible in self.cibles:
            _log.info("cible : %s (%s), %s, s/n %s", cible.nom, cible.disque.chemin,
                      cible.disque.description, cible.disque.serie)

        try:
            self._derouler()
        except _Interruption:
            pass  # chaque cible a déjà son verdict
        except BaseException as erreur:
            if isinstance(erreur, KeyboardInterrupt):
                self.arreter()
                motif = "opération interrompue"
            else:
                _log.exception("erreur interne")
                motif = f"erreur interne : {erreur!r}"
            for cible in self.cibles:
                cible.conclure(INTERROMPUE if self._arret.is_set() else ECHEC, motif)
            raise
        finally:
            for processus in list(self._processus):
                processus.tuer()
            self.source.liberer()
            self.duree = time.monotonic() - debut
            for cible in self.cibles:
                _log.info("verdict %s : %s %s", cible.nom, cible.etat, cible.motif)

        return self.cibles

    def _derouler(self) -> None:
        self.etape = self.source.etape_preparation
        try:
            self.source.preparer()
        except ErreurSource as erreur:
            for cible in self.cibles:
                cible.conclure(ECHEC, f"source : {erreur}")
            return
        self.table = self.source.table
        self.plans = self.source.plans
        self._interrompu()

        self.etape = "validation des cibles"
        self._valider()
        if not self.actives:
            return

        if self.source.brut:
            self.etape = "copie intégrale du disque"
            self._diffuser(self.source.ouvrir_disque_brut(self.journal),
                           self.source.taille_requise, "disque entier",
                           lambda c: c.disque.chemin)
            self._interrompu()
            for cible in self.actives:
                cible.conclure(REUSSIE)
            self.etape = "terminé"
            return

        self.etape = "tête du disque"
        tete = self._copier_tete()
        self._interrompu()

        self.etape = "table de partitions"
        self._ecrire_tables()
        self._interrompu()

        for rang, plan in enumerate(self.plans, start=1):
            if not self.actives:
                return
            self.etape = (f"partition {plan.entree.numero} ({rang} sur {len(self.plans)})"
                          f" — {plan.choix}")
            self._copier_partition(plan)
            self._interrompu()

        self.etape = "vérification"
        self._verifier(tete)

        for cible in self.actives:
            cible.conclure(REUSSIE)
        self.etape = "terminé"

    def _interrompu(self) -> None:
        if self._arret.is_set():
            for cible in self.cibles:
                cible.conclure(INTERROMPUE, f"interrompu pendant : {self.etape}")
            raise _Interruption()

    # ------------------------------------------------------------ préalable ---

    def _valider(self) -> None:
        """§6.5 : toutes les cibles sont validées avant qu'une seule soit touchée."""
        requis = self.source.taille_requise
        for cible in self.cibles:
            disque = cible.disque
            motif = ""
            if disque.taille < requis:
                motif = (f"trop petite : {_go(disque.taille)} pour {_go(requis)} requis")
            elif disque.secteur_logique != self.source.secteur:
                motif = (f"secteurs de {disque.secteur_logique} octets, "
                         f"la source en a de {self.source.secteur}")
            else:
                # Les filets de P2 se décident dans devices, et seulement là.
                motif = devices.refus_comme_cible(disque) or _essai_ouverture(disque.chemin)

            if motif:
                cible.conclure(ECARTEE, motif)
            else:
                cible.etat = EN_COURS

    # --------------------------------------------------------- préparation ---

    def _copier_tete(self) -> bytes:
        """La tête du disque source, diffusée telle quelle à toutes les cibles.

        Elle contient le code d'amorçage d'un MBR, et l'espace qu'un chargeur
        peut occuper avant la première partition. La table qu'elle contient
        aussi sera réécrite juste après, correctement, par sfdisk.
        """
        octets = self.table.debut_premiere_partition * self.table.secteur
        flux = self.source.ouvrir_tete()
        tete = os.pread(flux.fd, min(octets, 4096), 0)
        self._diffuser(flux, octets, "tête du disque", lambda c: c.disque.chemin)
        return tete

    def _ecrire_tables(self) -> None:
        sysexec.executer(["udevadm", "settle"], delai=60)
        for cible in self.actives:
            resultat = layout.reproduire(self.table, cible.disque.chemin)
            if not resultat.ok:
                cible.conclure(ECHEC, "écriture de la table impossible : "
                               + _derniere_ligne(resultat.erreur))

        # Le noyau crée les nouvelles partitions, udev leurs nœuds : attendre
        # qu'ils existent avant de relire chaque cible.
        sysexec.executer(["udevadm", "settle"], delai=60)
        attendus = [plan.entree.numero for plan in self.plans]
        for cible in self.actives:
            relu = devices.decrire(cible.disque.chemin)
            if relu is None:
                cible.conclure(ECHEC, "cible disparue après l'écriture de la table")
                continue
            cible.disque = relu
            cible.partitions = {p.numero: p.chemin for p in relu.partitions}
            manquantes = [n for n in attendus if n not in cible.partitions]
            if manquantes:
                cible.conclure(ECHEC, "partitions absentes après l'écriture de la table : "
                               + ", ".join(map(str, manquantes)))

    # --------------------------------------------------------------- copie ---

    def _copier_partition(self, plan: Plan) -> None:
        numero = plan.entree.numero
        moteur = plan.choix.moteur
        if plan.choix.avertissement:
            for cible in self.actives:
                cible.avertissements.append(f"partition {numero} : {plan.choix.raison}")

        if moteur == filesystems.AUCUN:
            return
        if moteur == filesystems.SWAP:
            self._recreer_swap(plan)
        elif moteur == filesystems.BRUT:
            octets = plan.entree.taille * self.table.secteur
            self._diffuser(self.source.flux_brut(plan, self.journal), octets,
                           f"partition {numero}", lambda c: c.partitions[numero])
        else:
            self._copier_partclone(plan)

        if plan.fstype == "ntfs" and moteur == filesystems.PARTCLONE:
            self._copier_secours_ntfs(plan)

    def _copier_partclone(self, plan: Plan) -> None:
        numero = plan.entree.numero
        programme = plan.choix.programme

        flux = self.source.flux_partclone(plan, self.journal)
        lecteur = self._suivre(flux.processus)

        ecrivains: dict[str, sysexec.Processus] = {}
        for cible in list(self.actives):
            nom = "".join(c for c in cible.nom if c.isalnum())  # « port2 », « devloop2 »
            try:
                ecrivains[cible.nom] = self._suivre(sysexec.Processus(
                    [programme, "-r", "-s", "-", "-o", cible.partitions[numero],
                     "-L", self.journal.fichier(f"p{numero}_{nom}_partclone.log")],
                    flux_entrant=True,
                    erreurs=self.journal.fichier(f"p{numero}_{nom}.err"),
                ))
            except OSError as erreur:
                cible.conclure(ECHEC, f"partition {numero} : {programme} impossible à lancer : {erreur}")

        cibles = [cible for cible in self.actives if cible.nom in ecrivains]
        diffusion = fanout.Diffusion(
            flux.fd,
            [fanout.Destination(cible.nom, ecrivains[cible.nom].entree) for cible in cibles],
            delai_blocage=self.delai_blocage,
        )
        self.diffusion = diffusion
        try:
            diffusion.executer()
        finally:
            self.diffusion = None
            # Un lecteur dont plus personne ne lit la sortie resterait bloqué.
            if not diffusion.fin_de_flux:
                lecteur.tuer()
            for ecrivain in ecrivains.values():
                ecrivain.fermer_entree()

        fin_lecteur = self._attendre(lecteur)

        for cible, suivi in zip(cibles, diffusion.cibles):
            ecrivain = ecrivains[cible.nom]
            if suivi.etat == BLOQUEE:
                ecrivain.tuer()
            fin_ecrivain = self._attendre(ecrivain)

            if suivi.etat == BLOQUEE:
                cible.conclure(BLOQUEE, f"partition {numero} : {suivi.motif}")
            elif suivi.etat == INTERROMPUE:
                cible.conclure(INTERROMPUE, f"partition {numero} : copie interrompue")
            elif not fin_lecteur.ok or diffusion.motif_source:
                cible.conclure(ECHEC, f"partition {numero} : lecture de la source impossible — "
                               + _derniere_ligne(fin_lecteur.erreur or diffusion.motif_source))
            elif not fin_ecrivain.ok:
                cible.conclure(ECHEC, f"partition {numero} : {programme} a échoué — "
                               + _derniere_ligne(fin_ecrivain.erreur))
            elif suivi.etat != REUSSIE:
                cible.conclure(ECHEC, f"partition {numero} : {suivi.motif}")

    def _copier_secours_ntfs(self, plan: Plan) -> None:
        """Le secteur d'amorçage de secours, que partclone ne copie pas
        (voir `filesystems.secours_ntfs`)."""
        numero = plan.entree.numero
        secours = self.source.secours_ntfs(plan)
        if secours is None:
            _log.warning("partition %d : pas de secteur d'amorçage de secours à recopier", numero)
            return
        position, contenu = secours
        for cible in self.actives:
            try:
                fd = sysexec.ouvrir(cible.partitions[numero], ecriture=True)
                try:
                    os.pwrite(fd, contenu, position)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError as erreur:
                cible.conclure(ECHEC, f"partition {numero} : secteur d'amorçage de secours "
                               f"non écrit — {erreur.strerror}")

    def _recreer_swap(self, plan: Plan) -> None:
        uuid, etiquette = self.source.swap(plan)
        argv = ["mkswap"]
        if uuid:
            argv += ["-U", uuid]
        if etiquette:
            argv += ["-L", etiquette]
        for cible in self.actives:
            resultat = sysexec.executer(argv + [cible.partitions[plan.entree.numero]])
            if not resultat.ok:
                cible.conclure(ECHEC, f"partition {plan.entree.numero} : mkswap a échoué — "
                               + _derniere_ligne(resultat.erreur))

    def _diffuser(self, flux: Flux, octets: int, quoi: str, chemin_de) -> None:
        """Copie brute de `octets` octets du flux vers le même emplacement de
        chaque cible."""
        lecteur = self._suivre(flux.processus) if flux.processus else None
        cibles = []
        fds = []
        try:
            for cible in self.actives:
                try:
                    fds.append(sysexec.ouvrir(chemin_de(cible), ecriture=True))
                    cibles.append(cible)
                except OSError as erreur:
                    cible.conclure(ECHEC, f"{quoi} : ouverture impossible — {erreur.strerror}")

            diffusion = fanout.Diffusion(
                flux.fd,
                [fanout.Destination(cible.nom, fd) for cible, fd in zip(cibles, fds)],
                limite=octets,
                delai_blocage=self.delai_blocage,
            )
            self.diffusion = diffusion
            try:
                diffusion.executer()
            finally:
                self.diffusion = None
                if lecteur and not diffusion.fin_de_flux:
                    lecteur.tuer()

            if lecteur:
                fin = self._attendre(lecteur)
                if not fin.ok and not diffusion.motif_source:
                    diffusion.motif_source = ("lecture de la source impossible — "
                                              + _derniere_ligne(fin.erreur))
            if diffusion.octets_lus < octets and not diffusion.motif_source:
                diffusion.motif_source = "source plus courte que prévu"
            for cible, suivi in zip(cibles, diffusion.cibles):
                if suivi.etat != REUSSIE:
                    cible.conclure(suivi.etat, f"{quoi} : {suivi.motif}")
                elif diffusion.motif_source:
                    cible.conclure(ECHEC, f"{quoi} : {diffusion.motif_source}")
        finally:
            for fd in fds:
                os.close(fd)
            if flux.a_fermer:
                os.close(flux.fd)

    def _suivre(self, processus: sysexec.Processus) -> sysexec.Processus:
        """Inscrit un programme pour qu'une interruption puisse l'arrêter."""
        self._processus.append(processus)
        return processus

    def _attendre(self, processus: sysexec.Processus) -> sysexec.Resultat:
        resultat = processus.attendre(DELAI_FIN_PROCESSUS)
        self._processus.remove(processus)
        return resultat

    # -------------------------------------------------------- vérification ---

    def _verifier(self, tete: bytes) -> None:
        sysexec.executer(["udevadm", "settle"], delai=60)
        fstypes = {
            plan.entree.numero: plan.fstype
            for plan in self.plans
            if plan.choix.moteur == filesystems.PARTCLONE
        }
        for cible in self.actives:
            relu = devices.decrire(cible.disque.chemin) or cible.disque
            problemes = verify.verifier(self.table, tete, relu, fstypes)
            if problemes:
                cible.conclure(ECHEC, "vérification : " + " ; ".join(problemes))


class _Interruption(Exception):
    """Levée pour sortir du déroulement après un arrêt demandé."""


def _essai_ouverture(chemin: str) -> str:
    """Le test d'ouverture du §6.5 : un disque protégé en écriture, par exemple."""
    try:
        fd = sysexec.ouvrir(chemin, ecriture=True)
    except OSError as erreur:
        if erreur.errno == errno.EBUSY:
            return devices.REFUS_SYSTEME
        return f"ouverture en écriture impossible : {erreur.strerror}"
    os.close(fd)
    return ""


def _derniere_ligne(texte: str) -> str:
    lignes = [ligne.strip() for ligne in (texte or "").splitlines() if ligne.strip()]
    return lignes[-1] if lignes else "sans message"


def _go(octets: int) -> str:
    return f"{octets / 1e9:.1f} Go".replace(".", ",")
