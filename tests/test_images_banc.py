"""Sauvegarde et restauration d'images, sur les disques en boucle du banc.

Exige root et un banc créé (`./outils/banc.sh creer`) ; ignoré sinon. Les
cibles du banc sont écrasées ; les images vont dans un dossier temporaire.

    python3 -m unittest -v tests.test_images_banc

Les critères de la phase 3 : aller-retour conforme dans les deux modes, image
altérée refusée avant toute écriture, dossier incomplet absent de la liste.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import unittest

from clonegator import devices, image, journal, layout, sysexec
from clonegator.engine import backup, clone
from clonegator.engine.sources import SourceImage

from tests.test_clone_banc import BOUCLES, MANIFESTE


def debut(chemin: str, octets: int = 64 * 1024 * 1024) -> str:
    """Empreinte du début d'un disque, pour prouver qu'il n'a pas été touché."""
    fd = sysexec.ouvrir(chemin)
    try:
        return hashlib.sha256(os.pread(fd, octets, 0)).hexdigest()
    finally:
        os.close(fd)


@unittest.skipUnless(BOUCLES and all(BOUCLES.values()), "banc absent ou pas root")
class ImagesSurLeBanc(unittest.TestCase):
    def setUp(self):
        self._journaux = tempfile.TemporaryDirectory()
        self._stockage = tempfile.TemporaryDirectory()
        journal.RACINE = self._journaux.name
        self.stockage = self._stockage.name

    def tearDown(self):
        self._journaux.cleanup()
        self._stockage.cleanup()

    def sauvegarder(self, source: str, etiquette: str, brut: bool = False) -> image.Image:
        with journal.Journal("sauvegarde-banc") as j:
            sauvegarde = backup.Sauvegarde(devices.decrire(BOUCLES[source]), self.stockage,
                                           etiquette, j, brut=brut)
            self.assertEqual(sauvegarde.executer(), backup.REUSSIE, sauvegarde.motif)
        return image.lire(sauvegarde.dossier)

    def restaurer(self, img: image.Image, cibles: list[str]) -> clone.Clonage:
        with journal.Journal("restauration-banc") as j:
            clonage = clone.Clonage(SourceImage(img),
                                    [devices.decrire(BOUCLES[nom]) for nom in cibles], j)
            clonage.executer()
        return clonage

    def test_aller_retour_automatique(self):
        img = self.sauvegarder("source-gpt", "banc")
        self.assertEqual(image.verifier_empreintes(img), "")
        self.assertTrue(os.path.exists(img.chemin(image.LISEZMOI)))

        clonage = self.restaurer(img, ["cible-grande", "cible-petite"])
        grande, petite = clonage.cibles
        self.assertEqual(petite.etat, clone.ECARTEE)
        self.assertEqual(grande.etat, clone.REUSSIE, grande.motif)
        self.assertManifesteConforme(grande.partitions[3])

    def test_image_alteree_refusee_avant_toute_ecriture(self):
        img = self.sauvegarder("source-gpt", "banc")
        fichier = img.chemin(img.partitions[-1]["fichier"])
        with open(fichier, "r+b") as f:
            f.truncate(os.path.getsize(fichier) - 1000)

        avant = debut(BOUCLES["cible-egale"])
        clonage = self.restaurer(img, ["cible-egale"])
        self.assertEqual(clonage.cibles[0].etat, clone.ECHEC)
        self.assertIn("altérée", clonage.cibles[0].motif)
        self.assertEqual(debut(BOUCLES["cible-egale"]), avant)

    def test_dossier_sans_metadonnees_absent_de_la_liste(self):
        img = self.sauvegarder("source-gpt", "banc")
        incomplet = img.dossier + "-incomplet"
        shutil.copytree(img.dossier, incomplet)
        os.remove(os.path.join(incomplet, image.METADONNEES))
        self.assertEqual([i.dossier for i in image.lister(self.stockage)], [img.dossier])

    def test_aller_retour_brut_numeros_non_contigus(self):
        img = self.sauvegarder("source-trous", "banc-brut", brut=True)
        self.assertEqual(img.mode, image.MODE_BRUT)
        clonage = self.restaurer(img, ["cible-sale"])
        self.assertEqual(clonage.cibles[0].etat, clone.REUSSIE, clonage.cibles[0].motif)
        taille = img.taille_requise
        self.assertEqual(debut(BOUCLES["cible-sale"], taille), debut(BOUCLES["source-trous"], taille))
        self.assertEqual([e.numero for e in layout.lire(BOUCLES["cible-sale"]).entrees], [1, 2, 3, 5])

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
