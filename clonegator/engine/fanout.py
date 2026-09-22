"""Diffusion : une source lue une seule fois, N destinations indépendantes.

C'est la partie que bash faisait mal. `tee` vers cinq disques, c'est un tampon
de 64 Ko commun à tous : le plus lent bride les autres à chaque à-coup, une
destination morte tue le tube entier, et une destination figée fige tout, sans
rien dire.

Ici, chaque destination a son propre fil d'écriture et sa propre file d'attente
bornée. Le fil de lecture dépose chaque bloc dans toutes les files ; un bloc
n'est jamais copié, les files partagent le même objet `bytes`.

Ce que ce module garantit (§6.4 de l'analyse) :

  - une destination en erreur est retirée, les autres continuent ;
  - une destination dont la file est pleine et qui n'a rien fait depuis
    `delai_blocage` secondes est déclarée bloquée et abandonnée ;
  - chaque destination a son propre verdict, il n'y a pas de verdict global.

Ce qu'il ne promet pas : le tampon absorbe la gigue, pas une lenteur durable.
Une destination réellement plus lente finit par brider la lecture, et donc les
autres. C'est physique, et c'est ce que demande l'analyse.

Ce module ne connaît ni les disques ni les partitions. Il reçoit des
descripteurs de fichiers ouverts — disque, fichier ou tube vers un processus —
et ne les ferme pas : ils appartiennent à l'appelant.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import stat
import threading
import time
from dataclasses import dataclass, field

_log = logging.getLogger("clonegator.fanout")

Mio = 1024 * 1024

EN_ATTENTE = "en attente"
EN_COURS = "en cours"
REUSSIE = "réussie"
ECHEC = "échec"
BLOQUEE = "bloquée"
INTERROMPUE = "interrompue"

# Marque de fin de flux déposée dans chaque file.
_FIN = None

# Période à laquelle le fil de lecture, bloqué sur une file pleine, revient
# vérifier l'état de la destination et le délai de blocage.
_SCRUTATION = 0.2


@dataclass
class Destination:
    """Un descripteur ouvert en écriture, et le nom sous lequel le rapporter."""

    nom: str
    fd: int


@dataclass
class Cible:
    """État d'une destination, lisible pendant la diffusion depuis un autre fil."""

    nom: str
    etat: str = EN_ATTENTE
    octets: int = 0
    motif: str = ""
    debut: float = 0.0
    fin: float = 0.0
    dernier_progres: float = 0.0

    @property
    def active(self) -> bool:
        return self.etat == EN_COURS

    @property
    def duree(self) -> float:
        if not self.debut:
            return 0.0
        return (self.fin or time.monotonic()) - self.debut

    @property
    def debit(self) -> float:
        """Octets par seconde depuis le début."""
        duree = self.duree
        return self.octets / duree if duree > 0 else 0.0


@dataclass
class _Voie:
    """Ce qui relie le fil de lecture à une destination. Interne."""

    destination: Destination
    cible: Cible
    file: queue.Queue
    synchroniser: bool
    verrou: threading.Lock = field(default_factory=threading.Lock)
    fil: threading.Thread | None = None

    def conclure(self, etat: str, motif: str = "") -> bool:
        """Fixe le verdict une seule fois. Revient à False s'il l'était déjà."""
        with self.verrou:
            if self.cible.etat != EN_COURS:
                return False
            self.cible.etat = etat
            self.cible.motif = motif
            self.cible.fin = time.monotonic()
        if etat == REUSSIE:
            _log.info("%s : %s, %d octets", self.cible.nom, etat, self.cible.octets)
        else:
            _log.warning("%s : %s — %s", self.cible.nom, etat, motif)
        return True


class Diffusion:
    """Une diffusion, du premier bloc lu au dernier verdict.

    `executer()` est bloquant ; les attributs `cibles`, `octets_lus` et
    `motif_source` peuvent être lus depuis un autre fil pour afficher la
    progression, et `arreter()` y être appelé pour interrompre.
    """

    def __init__(
        self,
        source: int,
        destinations: list[Destination],
        *,
        taille_bloc: int = 4 * Mio,
        tampon: int = 64 * Mio,
        delai_blocage: float = 60.0,
        synchro_tous_les: int = 256 * Mio,
        limite: int | None = None,
        empreinte: bool = False,
    ):
        """
        source            descripteur ouvert en lecture
        tampon            mémoire allouée à chaque destination
        delai_blocage     secondes sans aucune activité, file pleine, avant
                          d'abandonner une destination
        synchro_tous_les  les disques et fichiers sont synchronisés à ce rythme,
                          pour qu'une erreur d'écriture remonte pendant la copie
                          et non à la toute fin
        limite            nombre d'octets à diffuser au plus
        empreinte         calcule le SHA-256 de ce qui a été lu
        """
        self.source = source
        self.taille_bloc = taille_bloc
        self.delai_blocage = delai_blocage
        self.synchro_tous_les = synchro_tous_les
        self.limite = limite
        self.octets_lus = 0
        self.motif_source = ""
        self.empreinte_source: str | None = None

        self._hachage = hashlib.sha256() if empreinte else None
        self._arret = threading.Event()

        places = max(1, tampon // taille_bloc)
        self._voies = [
            _Voie(
                destination=destination,
                cible=Cible(nom=destination.nom),
                file=queue.Queue(maxsize=places),
                synchroniser=_synchronisable(destination.fd),
            )
            for destination in destinations
        ]

    @property
    def cibles(self) -> list[Cible]:
        return [voie.cible for voie in self._voies]

    def arreter(self) -> None:
        """Interrompt la diffusion. Les destinations en cours sont déclarées
        interrompues : une copie partielle n'est jamais présentée comme bonne."""
        self._arret.set()

    def executer(self) -> list[Cible]:
        maintenant = time.monotonic()
        for voie in self._voies:
            voie.cible.etat = EN_COURS
            voie.cible.debut = maintenant
            voie.cible.dernier_progres = maintenant
            voie.fil = threading.Thread(
                target=self._ecrire,
                args=(voie,),
                name=f"diffusion-{voie.cible.nom}",
                daemon=True,  # un fil figé sur un disque mort ne doit pas retenir le programme
            )
            voie.fil.start()

        try:
            self._lire()
        except BaseException as erreur:
            # Quoi qu'il arrive au fil de lecture, le flux est tronqué : aucune
            # destination ne doit pouvoir conclure à une réussite.
            if isinstance(erreur, KeyboardInterrupt):
                self._arret.set()
            elif not self.motif_source:
                self.motif_source = f"diffusion abandonnée : {erreur!r}"
            raise
        finally:
            self._terminer()

        if self._hachage is not None:
            self.empreinte_source = self._hachage.hexdigest()
        return self.cibles

    # ------------------------------------------------------------- lecture ---

    def _lire(self) -> None:
        while not self._arret.is_set():
            if not any(voie.cible.active for voie in self._voies):
                _log.warning("plus aucune destination active, lecture abandonnée")
                return

            taille = self.taille_bloc
            if self.limite is not None:
                taille = min(taille, self.limite - self.octets_lus)
                if taille <= 0:
                    return

            try:
                bloc = os.read(self.source, taille)
            except OSError as erreur:
                self.motif_source = f"lecture de la source impossible : {erreur}"
                _log.error("%s", self.motif_source)
                return

            if not bloc:
                return  # fin de flux

            self.octets_lus += len(bloc)
            if self._hachage is not None:
                self._hachage.update(bloc)

            for voie in self._voies:
                self._deposer(voie, bloc)

    def _deposer(self, voie: _Voie, bloc: bytes | None) -> None:
        """Remet un bloc à une destination, sans jamais attendre indéfiniment."""
        while voie.cible.active:
            try:
                voie.file.put(bloc, timeout=_SCRUTATION)
                return
            except queue.Full:
                pass
            if self._arret.is_set() and bloc is not _FIN:
                return
            self._verifier_blocage(voie)

    def _verifier_blocage(self, voie: _Voie) -> None:
        inactif = time.monotonic() - voie.cible.dernier_progres
        if inactif > self.delai_blocage:
            voie.conclure(BLOQUEE, f"aucune écriture depuis {inactif:.0f} s")

    # --------------------------------------------------------------- fin ---

    def _terminer(self) -> None:
        if self._arret.is_set():
            motif_global = (INTERROMPUE, "diffusion interrompue")
        elif self.motif_source:
            motif_global = (ECHEC, self.motif_source)
        else:
            motif_global = None

        if motif_global:
            for voie in self._voies:
                voie.conclure(*motif_global)
            return  # les fils voient leur verdict et s'arrêtent d'eux-mêmes

        # Fin normale : chaque destination vide sa file, se synchronise, et
        # conclut elle-même. On attend, sous la même surveillance de blocage.
        for voie in self._voies:
            self._deposer(voie, _FIN)

        for voie in self._voies:
            while voie.cible.active and voie.fil.is_alive():
                voie.fil.join(timeout=_SCRUTATION)
                self._verifier_blocage(voie)
            voie.conclure(ECHEC, "fil d'écriture arrêté sans verdict")

    # ------------------------------------------------------------ écriture ---

    def _ecrire(self, voie: _Voie) -> None:
        cible = voie.cible
        fd = voie.destination.fd
        depuis_synchro = 0

        try:
            while cible.active:
                try:
                    bloc = voie.file.get(timeout=_SCRUTATION)
                except queue.Empty:
                    continue

                cible.dernier_progres = time.monotonic()

                if bloc is _FIN:
                    if voie.synchroniser:
                        _synchroniser(fd)
                    voie.conclure(REUSSIE)
                    return

                _ecrire_tout(fd, bloc, voie)
                cible.octets += len(bloc)
                cible.dernier_progres = time.monotonic()

                depuis_synchro += len(bloc)
                if voie.synchroniser and depuis_synchro >= self.synchro_tous_les:
                    _synchroniser(fd)
                    cible.dernier_progres = time.monotonic()
                    depuis_synchro = 0

        except OSError as erreur:
            voie.conclure(ECHEC, _decrire(erreur))
        except Exception as erreur:
            _log.exception("%s : erreur interne", cible.nom)
            voie.conclure(ECHEC, f"erreur interne : {erreur!r}")
        finally:
            _vider(voie.file)


def _ecrire_tout(fd: int, bloc: bytes, voie: _Voie) -> None:
    """`os.write` peut écrire moins que demandé, notamment dans un tube."""
    vue = memoryview(bloc)
    while vue and voie.cible.active:
        ecrit = os.write(fd, vue)
        vue = vue[ecrit:]


def _synchronisable(fd: int) -> bool:
    """Un disque ou un fichier se synchronise ; un tube, non."""
    try:
        mode = os.fstat(fd).st_mode
    except OSError:
        return False
    return stat.S_ISBLK(mode) or stat.S_ISREG(mode)


def _synchroniser(fd: int) -> None:
    """Force l'écriture physique, puis libère le cache de ce qui a été écrit.

    Sans `fsync`, une erreur d'écriture ne remonterait qu'à la fermeture, et un
    disque défaillant passerait pour réussi. Libérer le cache ensuite évite que
    cinq disques de 500 Go ne se disputent la mémoire de la station.
    """
    os.fsync(fd)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass  # simple conseil au noyau, sans conséquence s'il est refusé


def _vider(file: queue.Queue) -> None:
    """Libère la mémoire d'une destination abandonnée."""
    try:
        while True:
            file.get_nowait()
    except queue.Empty:
        pass


def _decrire(erreur: OSError) -> str:
    if isinstance(erreur, BrokenPipeError):
        return "le programme destinataire s'est arrêté"
    return f"écriture impossible : {erreur.strerror or erreur}"
