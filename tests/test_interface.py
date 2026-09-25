"""La logique de l'interface (§9), sans écran : listes et formulaires.

    python3 -m unittest -v tests.test_interface
"""

from __future__ import annotations

import unittest

from clonegator.ui.model import RETOUR, VALIDER, Champ, Element, Formulaire, Liste


def liste_disques(multiple=False):
    return Liste("", [
        Element("SATA1", "sata1"),
        Element("USB2", "usb2", actif=False, motif="utilisé par le système"),
        Element("SATA2", "sata2"),
        Element("SATA3", "sata3", actif=False, motif="SMART défaillant", forcable=True,
                motif_force="forcé"),
    ], multiple=multiple)


class ChoixSimple(unittest.TestCase):
    def test_les_fleches_sautent_les_disques_grises(self):
        liste = liste_disques()
        self.assertEqual(liste.choix, "sata1")
        liste.touche("bas")
        self.assertEqual(liste.choix, "sata2")  # USB2 grisé, sauté
        liste.touche("bas")
        self.assertEqual(liste.choix, "sata1")  # on boucle, SATA3 grisé aussi

    def test_un_numero_choisit_et_valide(self):
        liste = liste_disques()
        self.assertEqual(liste.touche("3"), VALIDER)
        self.assertEqual(liste.choix, "sata2")

    def test_le_numero_d_un_disque_grise_ne_fait_rien(self):
        liste = liste_disques()
        self.assertIsNone(liste.touche("2"))
        self.assertEqual(liste.choix, "sata1")

    def test_echap_revient(self):
        self.assertEqual(liste_disques().touche("echap"), RETOUR)

    def test_le_motif_passe_avant_le_detail(self):
        element = Element("USB2", actif=False, motif="utilisé par le système", detail="GPT, 3 partitions")
        texte = Liste("", [element]).lignes()[0].texte()
        self.assertLess(texte.index("utilisé"), texte.index("GPT"))


class ChoixMultiple(unittest.TestCase):
    def test_espace_et_numero_cochent(self):
        liste = liste_disques(multiple=True)
        liste.touche("espace")
        liste.touche("3")
        self.assertEqual(liste.choix, ["sata1", "sata2"])
        liste.touche("3")
        self.assertEqual(liste.choix, ["sata1"])

    def test_entree_coche_comme_espace(self):
        liste = liste_disques(multiple=True)
        self.assertIsNone(liste.touche("entree"))
        self.assertEqual(liste.choix, ["sata1"])
        liste.touche("entree")
        self.assertEqual(liste.choix, [])

    def test_on_valide_sur_la_ligne_valider(self):
        liste = liste_disques(multiple=True)
        liste.touche("espace")
        liste.touche("haut")  # depuis le premier disque, on remonte sur « Valider »
        self.assertTrue(liste.sur_valider)
        self.assertEqual(liste.touche("entree"), VALIDER)
        self.assertEqual(liste.choix, ["sata1"])

    def test_valider_sans_rien_coche_refuse_poliment(self):
        liste = liste_disques(multiple=True)
        liste.touche("haut")
        self.assertIsNone(liste.touche("entree"))
        self.assertIn("Cochez", liste.message)

    def test_f_leve_seulement_le_refus_smart(self):
        liste = liste_disques(multiple=True)
        liste.touche("f")
        self.assertTrue(liste.elements[3].actif)
        self.assertEqual(liste.elements[3].motif, "forcé")
        self.assertFalse(liste.elements[1].actif)  # P2 ne se force pas


class Formulaires(unittest.TestCase):
    def test_saisie_effacement_et_validation(self):
        formulaire = Formulaire("", [Champ("Hôte", "10.0.0."), Champ("Mot de passe", masque=True)])
        formulaire.touche("1")
        formulaire.touche("2")
        formulaire.touche("effacer")
        self.assertIsNone(formulaire.touche("entree"))  # champ suivant
        for caractere in "secret":
            formulaire.touche(caractere)
        self.assertEqual(formulaire.touche("entree"), VALIDER)
        self.assertEqual(formulaire.valeurs, {"Hôte": "10.0.0.1", "Mot de passe": "secret"})

    def test_le_mot_de_passe_ne_s_affiche_jamais(self):
        formulaire = Formulaire("", [Champ("Mot de passe", "secret", masque=True)])
        self.assertNotIn("secret", formulaire.lignes()[0].texte())


if __name__ == "__main__":
    unittest.main()
