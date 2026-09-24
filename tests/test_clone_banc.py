"""Le clonage complet, sur les disques en boucle du banc d'essai.

Exige root et un banc créé (`./outils/banc.sh creer`) ; ignoré sinon. Les
cibles du banc sont écrasées.

    python3 -m unittest -v tests.test_clone_banc

Ce que les baies ne peuvent pas donner sans sacrifier le maître du port 1 :
une source numérotée 1, 2, 3, 5 et une cible plus petite que la source.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from clonegator import devices, journal, layout, sysexec
from clonegator.engine import clone

BANC = os.environ.get("CLONEGATOR_BANC", "/var/tmp/clonegator-banc")
MANIFESTE = os.path.join(BANC, "source-gpt.sha256")


def boucle(nom: str) -> str | None:
    """Le périphérique en boucle attaché à une image du banc, ou None."""
    resultat = sysexec.executer(["losetup", "-j", os.path.join(BANC, f"{nom}.img")])
    ligne = resultat.sortie.strip()
    return ligne.split(":", 1)[0] if ligne else None


NOMS = ["source-gpt", "source-trous", "cible-egale", "cible-grande", "cible-petite", "cible-sale"]
BOUCLES = {nom: boucle(nom) for nom in NOMS} if os.geteuid() == 0 else {}


@unittest.skipUnless(BOUCLES and all(BOUCLES.values()), "banc absent ou pas root")
class ClonageSurLeBanc(unittest.TestCase):
    def setUp(self):
        self._dossier = tempfile.TemporaryDirectory()
        journal.RACINE = self._dossier.name

    def tearDown(self):
        self._dossier.cleanup()

    def cloner(self, source: str, cibles: list[str]) -> clone.Clonage:
        disque_source = devices.decrire(BOUCLES[source])
        disques = [devices.decrire(BOUCLES[nom]) for nom in cibles]
        with journal.Journal("essai-banc") as j:
            clonage = clone.Clonage(disque_source, disques, j)
            clonage.executer()
        return clonage

    def test_source_gpt_cible_trop_petite_ecartee(self):
        clonage = self.cloner(
            "source-gpt", ["cible-egale", "cible-grande", "cible-petite", "cible-sale"]
        )
        etats = {c.disque.chemin: c.etat for c in clonage.cibles}
        self.assertEqual(etats[BOUCLES["cible-petite"]], clone.ECARTEE)
        for nom in ("cible-egale", "cible-grande", "cible-sale"):
            with self.subTest(cible=nom):
                cible = next(c for c in clonage.cibles if c.disque.chemin == BOUCLES[nom])
                self.assertEqual(cible.etat, clone.REUSSIE, cible.motif)
                self.assertManifesteConforme(cible.partitions[3])

    def test_source_aux_numeros_non_contigus(self):
        clonage = self.cloner("source-trous", ["cible-egale", "cible-sale"])
        source = layout.lire(BOUCLES["source-trous"])
        self.assertEqual([e.numero for e in source.entrees], [1, 2, 3, 5])
        for cible in clonage.cibles:
            with self.subTest(cible=cible.nom):
                self.assertEqual(cible.etat, clone.REUSSIE, cible.motif)
                relue = layout.lire(cible.disque.chemin)
                self.assertEqual(layout.comparer(source, relue), [])

    def assertManifesteConforme(self, partition: str):
        with tempfile.TemporaryDirectory() as montage:
            self.assertTrue(sysexec.executer(["mount", "-o", "ro", partition, montage]).ok)
            try:
                controle = sysexec.executer(
                    ["sh", "-c", f"cd {montage} && sha256sum --quiet -c {MANIFESTE}"], delai=120
                )
                self.assertTrue(controle.ok, controle.sortie + controle.erreur)
            finally:
                sysexec.executer(["umount", montage])


if __name__ == "__main__":
    unittest.main()
