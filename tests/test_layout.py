"""Lecture des tables de partitions depuis leur description texte, sans disque.

C'est ce que fait une restauration : l'image ne conserve que `disque.sfdisk`.

    python3 -m unittest -v tests.test_layout
"""

from __future__ import annotations

import unittest

from clonegator import layout

GPT = """label: gpt
label-id: A104BCA8-604B-47BB-A381-7B8220B9F83E
device: /dev/sdf
unit: sectors
first-lba: 34
last-lba: 937703054
sector-size: 512

/dev/sdf1 : start=        2048, size=      204800, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=695C4F0F-331D-43E2-9A55-399155C63679, name="EFI system partition", attrs="GUID:63"
/dev/sdf2 : start=      206848, size=       32768, type=E3C9E316-0B5C-4DB8-817D-F92DF00215AE, uuid=3F10F4FC-8324-4786-A716-06B15B2CB99A, name="Microsoft reserved partition", attrs="GUID:63"
/dev/sdf3 : start=      239616, size=   526694400, type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7, uuid=4C791465-0786-4836-B8B6-FA582DF0A152, name="Basic data partition", attrs="GUID:63"
/dev/sdf5 : start=   936534016, size=     1165312, type=DE94BBA4-06D1-4D40-A16A-BFD50179D6AC, uuid=06E24B2A-537E-4EC5-BFA1-BD26C744C971, attrs="RequiredPartition GUID:63"
"""

MBR = """label: dos
label-id: 0x1c2d3e4f
device: /dev/sdb
unit: sectors
sector-size: 512

/dev/sdb1 : start=        2048, size=     1024000, type=7, bootable
/dev/sdb2 : start=     1026048, size=     8000000, type=5
/dev/sdb5 : start=     1028096, size=     2000000, type=83
/dev/sdb6 : start=     3030144, size=     1000000, type=82
"""


class DescriptionGPT(unittest.TestCase):
    def setUp(self):
        self.table = layout.depuis_description(GPT)

    def test_entete(self):
        self.assertEqual(self.table.etiquette, "gpt")
        self.assertEqual(self.table.identifiant, "A104BCA8-604B-47BB-A381-7B8220B9F83E")
        self.assertEqual(self.table.secteur, 512)

    def test_numeros_reels(self):
        self.assertEqual([e.numero for e in self.table.entrees], [1, 2, 3, 5])

    def test_champs_entre_guillemets(self):
        esp = self.table.entree(1)
        self.assertEqual(esp.nom, "EFI system partition")
        self.assertEqual(esp.attributs, "GUID:63")
        self.assertEqual(self.table.entree(5).attributs, "RequiredPartition GUID:63")
        self.assertIsNone(self.table.entree(5).nom)

    def test_taille_requise_avec_gpt_de_secours(self):
        fin = 936534016 + 1165312
        self.assertEqual(self.table.taille_requise, (fin + layout.SECTEURS_GPT_SECOURS) * 512)

    def test_description_pour_cible_retire_ce_qui_est_propre_a_la_source(self):
        texte = layout.description_pour_cible(self.table)
        self.assertNotIn("device:", texte)
        self.assertNotIn("last-lba", texte)
        self.assertIn("label-id: A104BCA8", texte)
        self.assertIn('name="EFI system partition"', texte)


class DescriptionMBR(unittest.TestCase):
    def setUp(self):
        self.table = layout.depuis_description(MBR)

    def test_etendue_et_logiques(self):
        self.assertEqual(self.table.etiquette, "dos")
        self.assertEqual([e.numero for e in self.table.entrees], [1, 2, 5, 6])
        self.assertTrue(self.table.entree(2).etendue)
        self.assertFalse(self.table.entree(5).etendue)

    def test_pas_de_gpt_de_secours(self):
        fin = 1026048 + 8000000
        self.assertEqual(self.table.taille_requise, fin * 512)


class DescriptionInvalide(unittest.TestCase):
    def test_table_inconnue(self):
        with self.assertRaises(layout.ErreurTable):
            layout.depuis_description("label: sun\n")

    def test_ligne_tronquee(self):
        with self.assertRaises(layout.ErreurTable):
            layout.depuis_description("label: gpt\n/dev/sda1 : size=100, type=83\n")


if __name__ == "__main__":
    unittest.main()
