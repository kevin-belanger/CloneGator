"""Unique point de passage vers le système.

Aucun autre module du projet n'appelle `subprocess` ni ne lit `/dev`, `/sys` ou
`/proc` directement. Tout passe par ici, ce qui donne trois choses :

  - un délai d'attente sur *chaque* commande. `clonesrv` pouvait se figer
    indéfiniment sur un disque bloqué, sans rien afficher ;
  - le journal verbatim des commandes réellement lancées ;
  - un point unique à remplacer pour faire tourner le reste du logiciel
    ailleurs que sur du vrai matériel.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

_log = logging.getLogger("clonegator.sysexec")

# Volontairement court : toutes les commandes qui passent par ici sont des
# commandes d'inspection. Les copies longues ont leur propre délai, explicite.
DELAI_DEFAUT = 30.0


@dataclass
class Resultat:
    """Ce qu'une commande externe a produit. N'est jamais une exception."""

    argv: list[str]
    code: int
    sortie: str
    erreur: str
    duree: float
    expire: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.expire

    @property
    def commande(self) -> str:
        return " ".join(self.argv)

    def __str__(self) -> str:
        if self.expire:
            etat = f"EXPIRE apres {self.duree:.1f}s"
        else:
            etat = f"code={self.code} en {self.duree:.2f}s"
        return f"{self.commande} -> {etat}"


class Processus:
    """Un programme qui produit ou consomme un flux : `partclone`, surtout.

    Pas de délai global ici : une copie dure le temps qu'elle dure, et c'est la
    diffusion qui surveille le blocage. En revanche, `attendre` borne l'attente
    de la fin du programme, une fois son flux terminé.

    Sa sortie d'erreur va dans un fichier du journal d'opération : `partclone`
    y écrit sa progression en continu, et un tube non lu finirait par le figer.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        flux_entrant: bool = False,
        flux_sortant: bool = False,
        entree_fd: int | None = None,
        erreurs: str | None = None,
    ):
        """
        flux_entrant  on écrira dans son entrée (`entree`)
        flux_sortant  on lira sa sortie (`sortie`)
        entree_fd     son entrée est ce descripteur : la sortie d'un autre
                      programme, ou un disque ouvert. Les deux programmes se
                      parlent alors directement, sans passer par Python.
        """
        self.argv = list(argv)
        self.erreurs = erreurs
        self._fichier_erreurs = open(erreurs, "wb") if erreurs else subprocess.DEVNULL
        self._debut = time.monotonic()
        _log.info("lancé : %s", self.commande)
        try:
            if entree_fd is not None:
                entree = entree_fd
            elif flux_entrant:
                entree = subprocess.PIPE
            else:
                entree = subprocess.DEVNULL
            self._popen = subprocess.Popen(
                self.argv,
                stdin=entree,
                stdout=subprocess.PIPE if flux_sortant else subprocess.DEVNULL,
                stderr=self._fichier_erreurs,
            )
        except OSError:
            self._fermer_journal()
            raise

    @property
    def commande(self) -> str:
        return " ".join(self.argv)

    @property
    def entree(self) -> int:
        """Descripteur où écrire ce que le programme lit."""
        return self._popen.stdin.fileno()

    @property
    def sortie(self) -> int:
        """Descripteur où lire ce que le programme produit."""
        return self._popen.stdout.fileno()

    def ceder_sortie(self) -> None:
        """Ferme notre copie de sa sortie, une fois confiée à un autre programme.

        Sans ça, si le programme en aval meurt, celui-ci ne reçoit jamais
        SIGPIPE — nous tenons encore le tube ouvert — et reste bloqué.
        """
        if self._popen.stdout and not self._popen.stdout.closed:
            self._popen.stdout.close()

    def fermer_entree(self) -> None:
        """Signale la fin du flux au programme."""
        if self._popen.stdin and not self._popen.stdin.closed:
            try:
                self._popen.stdin.close()
            except BrokenPipeError:
                pass  # le programme est déjà parti ; son code de retour le dira

    def attendre(self, delai: float) -> Resultat:
        """Attend la fin du programme ; au-delà du délai, il est tué."""
        expire = False
        try:
            code = self._popen.wait(timeout=delai)
        except subprocess.TimeoutExpired:
            self.tuer()
            expire = True
            try:
                code = self._popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Tué mais toujours là : coincé dans le noyau sur un disque qui
                # ne répond plus. On l'abandonne plutôt que de figer la station.
                _log.error("%s ne se termine pas, même tué : abandonné", self.commande)
                code = -9
        if self._popen.stdout:
            self._popen.stdout.close()
        self._fermer_journal()

        resultat = Resultat(
            argv=self.argv,
            code=code,
            sortie="",
            erreur=self.fin_du_journal(),
            duree=time.monotonic() - self._debut,
            expire=expire,
        )
        if resultat.ok:
            _log.info("%s", resultat)
        else:
            _log.warning("%s | %s", resultat, resultat.erreur[-300:])
        return resultat

    def tuer(self) -> None:
        if self._popen.poll() is None:
            _log.warning("tué : %s", self.commande)
            self._popen.kill()

    def fin_du_journal(self, octets: int = 2000) -> str:
        """Les dernières lignes de sa sortie d'erreur, pour nommer un échec."""
        if not self.erreurs:
            return ""
        try:
            with open(self.erreurs, "rb") as fichier:
                fichier.seek(0, os.SEEK_END)
                fichier.seek(max(0, fichier.tell() - octets))
                brut = fichier.read()
        except OSError:
            return ""
        # partclone réécrit sa ligne de progression avec \r : ne garder que
        # l'état final de chaque ligne.
        lignes = [ligne.split("\r")[-1].strip() for ligne in _texte(brut).split("\n")]
        return "\n".join(ligne for ligne in lignes if ligne)

    def _fermer_journal(self) -> None:
        if self._fichier_erreurs is not subprocess.DEVNULL:
            self._fichier_erreurs.close()


def disponible(programme: str) -> bool:
    """Le programme est-il installé ?"""
    return shutil.which(programme) is not None


def executer(
    argv: list[str],
    delai: float = DELAI_DEFAUT,
    entree: str | None = None,
    dossier: str | None = None,
) -> Resultat:
    """Lance une commande et rapporte ce qu'elle a fait.

    Ne lève jamais d'exception sur un code de retour non nul : c'est à
    l'appelant de décider si l'échec est grave. Un dépassement de délai tue le
    processus et revient avec `expire=True`.
    """
    debut = time.monotonic()
    try:
        fin = subprocess.run(
            argv,
            input=entree,
            cwd=dossier,
            capture_output=True,
            text=True,
            timeout=delai,
            check=False,
        )
    except subprocess.TimeoutExpired as expiration:
        duree = time.monotonic() - debut
        resultat = Resultat(
            argv=list(argv),
            code=-1,
            sortie=_texte(expiration.stdout),
            erreur=_texte(expiration.stderr),
            duree=duree,
            expire=True,
        )
        _log.warning("%s", resultat)
        return resultat
    except FileNotFoundError:
        duree = time.monotonic() - debut
        resultat = Resultat(
            argv=list(argv),
            code=127,
            sortie="",
            erreur=f"{argv[0]} : introuvable",
            duree=duree,
        )
        _log.warning("%s", resultat)
        return resultat

    resultat = Resultat(
        argv=list(argv),
        code=fin.returncode,
        sortie=fin.stdout or "",
        erreur=fin.stderr or "",
        duree=time.monotonic() - debut,
    )

    if resultat.ok:
        _log.debug("%s", resultat)
    else:
        _log.warning("%s | %s", resultat, resultat.erreur.strip()[:200])

    return resultat


def executer_json(argv: list[str], delai: float = DELAI_DEFAUT) -> dict | None:
    """Comme `executer`, mais pour les commandes qui savent sortir du JSON.

    `lsblk --json` et `sfdisk --json` couvrent l'essentiel de nos besoins de
    lecture — c'est une des raisons du choix de Python (§16 de l'analyse).
    Revient à None si la commande échoue ou si la sortie n'est pas exploitable.
    """
    resultat = executer(argv, delai=delai)
    if not resultat.ok:
        return None

    try:
        return json.loads(resultat.sortie)
    except json.JSONDecodeError as erreur:
        _log.warning("%s : sortie JSON illisible (%s)", resultat.commande, erreur)
        return None


def lister(repertoire: str) -> list[str]:
    """Noms des entrées d'un répertoire système, triés ; vide s'il n'existe pas."""
    try:
        return sorted(os.listdir(repertoire))
    except OSError as erreur:
        _log.debug("%s illisible (%s)", repertoire, erreur)
        return []


def chemin_reel(chemin: str) -> str | None:
    """Cible finale d'un lien comme ceux de `/dev/disk/by-path`, None si illisible."""
    try:
        return os.path.realpath(chemin, strict=True)
    except OSError as erreur:
        _log.warning("lien illisible : %s (%s)", chemin, erreur)
        return None


def ouvrir(chemin: str, ecriture: bool = False) -> int:
    """Ouvre un disque ou un fichier et rend son descripteur.

    En écriture, un disque est ouvert en exclusivité (O_EXCL) : le noyau refuse
    si quelqu'un d'autre le tient déjà. Lève OSError en cas d'échec ; le
    descripteur appartient à l'appelant, qui le ferme.
    """
    if ecriture:
        drapeaux = os.O_WRONLY | os.O_EXCL
    else:
        drapeaux = os.O_RDONLY
    fd = os.open(chemin, drapeaux | os.O_CLOEXEC)
    _log.debug("ouvert %s en %s (fd %d)", chemin, "écriture" if ecriture else "lecture", fd)
    return fd


def _texte(brut: bytes | str | None) -> str:
    if brut is None:
        return ""
    if isinstance(brut, bytes):
        return brut.decode("utf-8", errors="replace")
    return brut
