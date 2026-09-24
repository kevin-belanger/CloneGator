"""Le moteur de diffusion, éprouvé sans aucun disque.

Les destinations sont des fichiers ou des tubes lus par des fils qui calculent
une empreinte : on peut ainsi tuer, figer ou saturer une destination à la
demande, et vérifier que les autres n'en savent rien.

    python3 -m unittest -v tests.test_fanout

`CLONEGATOR_VOLUME_ESSAI` (en Mio, 64 par défaut) fixe le volume diffusé dans
les essais par tubes. Le critère de la phase 1 se vérifie avec plusieurs Go :

    CLONEGATOR_VOLUME_ESSAI=4096 python3 -m unittest -v tests.test_fanout
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
import unittest

from clonegator.engine import fanout
from clonegator.engine.fanout import Destination, Diffusion

Mio = fanout.Mio
VOLUME = int(os.environ.get("CLONEGATOR_VOLUME_ESSAI", "64")) * Mio


class SourceGeneree:
    """Un tube alimenté par un fil qui produit `volume` octets pseudo-aléatoires.

    Pas de fichier sur disque : un essai de plusieurs Go ne coûte que du
    processeur. L'empreinte de ce qui a été produit sert de référence.
    """

    def __init__(self, volume: int, bloc: int = Mio, pause: float = 0.0):
        self.volume = volume
        self.bloc = bloc
        self.pause = pause
        self.empreinte = hashlib.sha256()
        self.lecture, self._ecriture = os.pipe()
        self._fil = threading.Thread(target=self._produire, daemon=True)
        self._fil.start()

    def _produire(self) -> None:
        # Un motif rapide à produire mais jamais identique d'un bloc à l'autre,
        # pour qu'un bloc perdu ou dupliqué change l'empreinte.
        motif = os.urandom(self.bloc)
        reste, numero = self.volume, 0
        try:
            while reste > 0:
                morceau = numero.to_bytes(8, "little") + motif[8 : min(self.bloc, reste)]
                morceau = morceau[: min(self.bloc, reste)]
                self.empreinte.update(morceau)
                os.write(self._ecriture, morceau)
                reste -= len(morceau)
                numero += 1
                if self.pause:
                    time.sleep(self.pause)
        except BrokenPipeError:
            pass
        finally:
            os.close(self._ecriture)

    def fermer(self) -> None:
        os.close(self.lecture)


class Consommateur:
    """Le bout lecteur d'un tube de destination, qui calcule une empreinte.

    `mourir_apres` : ferme le tube après tant d'octets, comme un processus qui
    plante. `figer_apres` : cesse de lire, comme un disque qui ne répond plus.
    """

    def __init__(self, nom: str, mourir_apres: int | None = None, figer_apres: int | None = None):
        self.nom = nom
        self.mourir_apres = mourir_apres
        self.figer_apres = figer_apres
        self.empreinte = hashlib.sha256()
        self.recu = 0
        self._lecture, self.ecriture = os.pipe()
        self.liberer = threading.Event()
        self._fil = threading.Thread(target=self._consommer, daemon=True)
        self._fil.start()

    @property
    def destination(self) -> Destination:
        return Destination(self.nom, self.ecriture)

    def _consommer(self) -> None:
        try:
            while True:
                if self.figer_apres is not None and self.recu >= self.figer_apres:
                    self.liberer.wait()
                    return
                if self.mourir_apres is not None and self.recu >= self.mourir_apres:
                    return
                morceau = os.read(self._lecture, Mio)
                if not morceau:
                    return
                self.empreinte.update(morceau)
                self.recu += len(morceau)
        finally:
            os.close(self._lecture)

    def attendre(self) -> None:
        self._fil.join(timeout=30)

    def fermer(self) -> None:
        os.close(self.ecriture)


def diffuser(source: SourceGeneree, consommateurs: list[Consommateur], **reglages) -> Diffusion:
    """Lance une diffusion puis ferme les tubes, comme le ferait l'appelant réel."""
    reglages.setdefault("empreinte", True)
    diffusion = Diffusion(
        source.lecture,
        [consommateur.destination for consommateur in consommateurs],
        **reglages,
    )
    try:
        diffusion.executer()
    finally:
        for consommateur in consommateurs:
            consommateur.fermer()
        for consommateur in consommateurs:
            consommateur.liberer.set()
            consommateur.attendre()
        source.fermer()
    return diffusion


def etats(diffusion: Diffusion) -> dict[str, str]:
    return {cible.nom: cible.etat for cible in diffusion.cibles}


class CinqDestinations(unittest.TestCase):
    """Le critère de la phase 1, dans le cas nominal."""

    def test_cinq_empreintes_identiques_a_la_source(self):
        source = SourceGeneree(VOLUME)
        consommateurs = [Consommateur(f"port {n}") for n in range(2, 7)]

        diffusion = diffuser(source, consommateurs)

        attendue = source.empreinte.hexdigest()
        self.assertEqual(diffusion.empreinte_source, attendue)
        self.assertEqual(diffusion.octets_lus, VOLUME)
        for consommateur, cible in zip(consommateurs, diffusion.cibles):
            with self.subTest(cible=cible.nom):
                self.assertEqual(cible.etat, fanout.REUSSIE, cible.motif)
                self.assertEqual(cible.octets, VOLUME)
                self.assertEqual(consommateur.empreinte.hexdigest(), attendue)

    def test_vers_des_fichiers(self):
        """Des fichiers ordinaires : la synchronisation finale est exercée."""
        source = SourceGeneree(32 * Mio)
        with tempfile.TemporaryDirectory() as dossier:
            chemins = [os.path.join(dossier, f"cible{n}") for n in range(3)]
            fds = [os.open(chemin, os.O_WRONLY | os.O_CREAT) for chemin in chemins]
            try:
                diffusion = Diffusion(
                    source.lecture,
                    [Destination(os.path.basename(c), fd) for c, fd in zip(chemins, fds)],
                )
                diffusion.executer()
            finally:
                for fd in fds:
                    os.close(fd)
                source.fermer()

            attendue = source.empreinte.hexdigest()
            for chemin, cible in zip(chemins, diffusion.cibles):
                self.assertEqual(cible.etat, fanout.REUSSIE, cible.motif)
                with open(chemin, "rb") as fichier:
                    self.assertEqual(hashlib.sha256(fichier.read()).hexdigest(), attendue)

    def test_source_vide(self):
        source = SourceGeneree(0)
        consommateurs = [Consommateur("seule")]
        diffusion = diffuser(source, consommateurs)
        self.assertEqual(etats(diffusion), {"seule": fanout.REUSSIE})
        self.assertEqual(diffusion.octets_lus, 0)

    def test_limite(self):
        source = SourceGeneree(20 * Mio)
        consommateurs = [Consommateur("a"), Consommateur("b")]
        diffusion = diffuser(source, consommateurs, limite=5 * Mio + 17)
        for consommateur, cible in zip(consommateurs, diffusion.cibles):
            self.assertEqual(cible.etat, fanout.REUSSIE)
            self.assertEqual(consommateur.recu, 5 * Mio + 17)


class LectureDepuisUnTube(unittest.TestCase):
    """Un tube ne rend que ce qu'il contient : le moteur doit remplir ses blocs."""

    def test_blocs_pleins_depuis_un_tube(self):
        source = SourceGeneree(10 * Mio, bloc=Mio)
        try:
            tailles = []
            while True:
                bloc = fanout._remplir(source.lecture, 4 * Mio)
                if not bloc:
                    break
                tailles.append(len(bloc))
        finally:
            source.fermer()
        self.assertEqual(tailles, [4 * Mio, 4 * Mio, 2 * Mio])


class UneDestinationTombe(unittest.TestCase):
    """Une destination qui disparaît ne doit rien coûter aux autres."""

    def test_destination_tuee_en_cours_de_route(self):
        source = SourceGeneree(VOLUME)
        consommateurs = [Consommateur(f"port {n}") for n in range(2, 6)]
        consommateurs.append(Consommateur("port 6", mourir_apres=VOLUME // 3))

        diffusion = diffuser(source, consommateurs)

        attendue = source.empreinte.hexdigest()
        *saines, morte = diffusion.cibles
        for consommateur, cible in zip(consommateurs, saines):
            with self.subTest(cible=cible.nom):
                self.assertEqual(cible.etat, fanout.REUSSIE, cible.motif)
                self.assertEqual(consommateur.empreinte.hexdigest(), attendue)
        self.assertEqual(morte.etat, fanout.ECHEC)
        self.assertIn("arrêté", morte.motif)
        self.assertLess(morte.octets, VOLUME)

    def test_erreur_d_ecriture(self):
        """/dev/full refuse toute écriture : ENOSPC, comme un disque plein."""
        source = SourceGeneree(16 * Mio)
        saine = Consommateur("saine")
        plein = os.open("/dev/full", os.O_WRONLY)
        try:
            diffusion = Diffusion(
                source.lecture,
                [saine.destination, Destination("pleine", plein)],
                empreinte=True,
            )
            diffusion.executer()
        finally:
            os.close(plein)
            saine.fermer()
            saine.attendre()
            source.fermer()

        self.assertEqual(
            etats(diffusion), {"saine": fanout.REUSSIE, "pleine": fanout.ECHEC}
        )
        self.assertEqual(saine.empreinte.hexdigest(), source.empreinte.hexdigest())
        self.assertIn("space", diffusion.cibles[1].motif.lower())

    def test_toutes_les_destinations_tombent(self):
        """La lecture s'arrête d'elle-même : inutile de lire 500 Go pour personne."""
        source = SourceGeneree(VOLUME * 4)
        consommateurs = [Consommateur(n, mourir_apres=2 * Mio) for n in "ab"]
        diffusion = diffuser(source, consommateurs)
        self.assertEqual(etats(diffusion), {"a": fanout.ECHEC, "b": fanout.ECHEC})
        self.assertLess(diffusion.octets_lus, VOLUME * 4)


class UneDestinationSeFige(unittest.TestCase):
    """Une destination qui ne répond plus est abandonnée, pas attendue."""

    def test_destination_figee_declaree_bloquee(self):
        source = SourceGeneree(VOLUME)
        consommateurs = [Consommateur("vive"), Consommateur("figée", figer_apres=Mio)]

        debut = time.monotonic()
        diffusion = diffuser(
            source, consommateurs, tampon=8 * Mio, taille_bloc=Mio, delai_blocage=1.0
        )
        duree = time.monotonic() - debut

        vive, figee = diffusion.cibles
        self.assertEqual(vive.etat, fanout.REUSSIE, vive.motif)
        self.assertEqual(consommateurs[0].empreinte.hexdigest(), source.empreinte.hexdigest())
        self.assertEqual(figee.etat, fanout.BLOQUEE)
        self.assertIn("aucune écriture", figee.motif)
        self.assertLess(duree, 15)

    def test_source_lente_n_est_pas_un_blocage(self):
        """Une destination qui attend la source n'est pas figée : elle n'a rien à faire."""
        source = SourceGeneree(6 * Mio, bloc=Mio, pause=0.4)
        consommateurs = [Consommateur("patiente")]
        diffusion = diffuser(source, consommateurs, taille_bloc=Mio, delai_blocage=0.3)
        self.assertEqual(etats(diffusion), {"patiente": fanout.REUSSIE})


class LaSourceTombe(unittest.TestCase):
    def test_source_illisible(self):
        """Lire un répertoire échoue (EISDIR) : toutes les cibles sont invalides."""
        repertoire = os.open(tempfile.gettempdir(), os.O_RDONLY)
        consommateurs = [Consommateur("a"), Consommateur("b")]
        try:
            diffusion = Diffusion(repertoire, [c.destination for c in consommateurs])
            diffusion.executer()
        finally:
            os.close(repertoire)
            for consommateur in consommateurs:
                consommateur.fermer()
                consommateur.attendre()

        self.assertEqual(etats(diffusion), {"a": fanout.ECHEC, "b": fanout.ECHEC})
        self.assertIn("source", diffusion.cibles[0].motif)

    def test_interruption(self):
        source = SourceGeneree(VOLUME * 4, pause=0.01)
        consommateurs = [Consommateur("a"), Consommateur("b")]
        diffusion = Diffusion(source.lecture, [c.destination for c in consommateurs])
        threading.Timer(0.3, diffusion.arreter).start()
        try:
            diffusion.executer()
        finally:
            for consommateur in consommateurs:
                consommateur.fermer()
                consommateur.attendre()
            source.fermer()

        self.assertEqual(
            etats(diffusion), {"a": fanout.INTERROMPUE, "b": fanout.INTERROMPUE}
        )


if __name__ == "__main__":
    unittest.main()
