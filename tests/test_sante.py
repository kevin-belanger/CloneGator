"""Le résumé SMART (§12), sur des rapports types de smartctl, sans disque.

    python3 -m unittest -v tests.test_sante
"""

from __future__ import annotations

import unittest

from clonegator import health


def ata(passed=True, **attributs):
    """Un rapport ATA : `attributs` associe un identifiant à (valeur, brut)."""
    table = [{"id": ident, "value": valeur, "raw": {"value": brut}}
             for ident, (valeur, brut) in ((int(k[1:]), v) for k, v in attributs.items())]
    return {"smart_status": {"passed": passed}, "ata_smart_attributes": {"table": table}}


class ResumeSmart(unittest.TestCase):
    def test_sa400_neuf(self):
        # Relevé sur un disque des baies : 99 % de vie, aucun secteur abîmé.
        self.assertEqual(health.resumer(ata(a231=(99, 99), a187=(100, 0))).niveau, health.OK)

    def test_verdict_defaillant(self):
        self.assertEqual(health.resumer(ata(passed=False)).niveau, health.DEFAILLANT)

    def test_secteurs_realloues(self):
        sante = health.resumer(ata(a5=(100, 12)))
        self.assertEqual(sante.niveau, health.USURE)
        self.assertIn("12 secteurs réalloués", sante.detail)

    def test_ssd_en_fin_de_vie(self):
        self.assertEqual(health.resumer(ata(a231=(15, 15))).niveau, health.USURE)

    def test_nvme(self):
        alerte = {"smart_status": {"passed": True},
                  "nvme_smart_health_information_log": {"critical_warning": 4}}
        self.assertEqual(health.resumer(alerte).niveau, health.DEFAILLANT)
        use = {"smart_status": {"passed": True},
               "nvme_smart_health_information_log": {"critical_warning": 0, "percentage_used": 85}}
        self.assertEqual(health.resumer(use).niveau, health.USURE)

    def test_adaptateur_usb_muet(self):
        # smartctl n'obtient rien d'un adaptateur qui ne transmet pas SMART.
        self.assertEqual(health.resumer({}).niveau, health.INCONNU)


if __name__ == "__main__":
    unittest.main()
