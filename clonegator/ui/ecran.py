"""Dessin curses et lecture des touches (§9 de l'analyse).

Ce module ne décide de rien : il dessine les structures de `model.py` et leur
passe les touches, traduites en mots (« haut », « entree », « echap »…). Chaque
interaction — choisir dans une liste, remplir un formulaire, lire un rapport —
est une méthode bloquante, qui rend la réponse de l'opérateur.

Les couleurs soulignent, elles ne portent jamais seules une information : un
état est toujours écrit en toutes lettres.
"""

from __future__ import annotations

import curses
import time

from .. import VERSION
from .model import (
    AIDE, AVERTISSEMENT, ECHEC, FORT, GRISE, NORMAL, OK, RETOUR, TITRE, VALIDER,
    Element, Formulaire, Ligne, Liste, Page,
)

_TOUCHES = {
    curses.KEY_UP: "haut",
    curses.KEY_DOWN: "bas",
    curses.KEY_ENTER: "entree",
    curses.KEY_BACKSPACE: "effacer",
    curses.KEY_RESIZE: "redim",
    curses.KEY_PPAGE: "haut",
    curses.KEY_NPAGE: "bas",
}
_CARACTERES = {
    "\n": "entree", "\r": "entree", " ": "espace", "\x1b": "echap",
    "\x7f": "effacer", "\b": "effacer", "\t": "tab",
    "\x03": "echap",  # Ctrl-C, en mode brut : un retour, jamais un arrêt brutal
}


class Ecran:
    def __init__(self, fenetre):
        self.fenetre = fenetre
        self.fenetre.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self._styles = {
            NORMAL: curses.A_NORMAL,
            FORT: curses.A_BOLD,
            TITRE: curses.A_BOLD | curses.A_REVERSE,
            GRISE: curses.A_DIM,
            AIDE: curses.A_DIM,
            OK: curses.A_BOLD,
            ECHEC: curses.A_BOLD,
            AVERTISSEMENT: curses.A_BOLD,
        }
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                fond = -1
            except curses.error:
                fond = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_GREEN, fond)
            curses.init_pair(2, curses.COLOR_RED, fond)
            curses.init_pair(3, curses.COLOR_YELLOW, fond)
            curses.init_pair(4, curses.COLOR_CYAN, fond)
            self._styles[OK] |= curses.color_pair(1)
            self._styles[ECHEC] |= curses.color_pair(2)
            self._styles[AVERTISSEMENT] |= curses.color_pair(3)
            self._styles[AIDE] = curses.color_pair(4)

    # ---------------------------------------------------------------- touches ---

    def touche(self, delai: float | None = None) -> str | None:
        """La prochaine touche, traduite ; None si le délai (en secondes) passe."""
        self.fenetre.timeout(-1 if delai is None else int(delai * 1000))
        try:
            brute = self.fenetre.get_wch()
        except curses.error:
            return None
        if isinstance(brute, int):
            return _TOUCHES.get(brute)
        return _CARACTERES.get(brute, brute)

    # ----------------------------------------------------------------- dessin ---

    def dessiner(self, page: Page, corps: list[Ligne], choisie: int | None = None,
                 message: str = "", style_message: str = AVERTISSEMENT) -> None:
        self.fenetre.erase()
        hauteur, largeur = self.fenetre.getmaxyx()
        # La bande du haut ne porte que le nom du logiciel ; l'intitulé de la
        # vue a sa propre ligne, juste en dessous.
        self._ecrire(0, Ligne.de(f" CloneGator {VERSION}".ljust(largeur), TITRE), largeur)
        self._ecrire(2, Ligne.de(f" {page.titre}", FORT), largeur)

        y = 4
        for ligne in page.entete:
            if y >= hauteur - 3:
                break
            self._ecrire(y, ligne, largeur)
            y += 1
        if page.entete:
            y += 1

        # Le corps défile pour garder la ligne choisie visible.
        place = max(1, hauteur - y - 3)
        debut = 0
        if choisie is not None and choisie >= place:
            debut = choisie - place + 1
        for rang, ligne in enumerate(corps[debut:debut + place]):
            self._ecrire(y + rang, ligne, largeur, surligner=(debut + rang == choisie))

        if message:
            self._ecrire(hauteur - 3, Ligne.de(message, style_message), largeur)
        if page.aide:
            self._ecrire(hauteur - 1, Ligne.de(page.aide, AIDE), largeur)
        self.fenetre.refresh()

    def _ecrire(self, y: int, ligne: Ligne, largeur: int, surligner: bool = False) -> None:
        x = 0
        for texte, style in ligne.morceaux:
            if x >= largeur - 1:
                break
            attribut = self._styles.get(style, curses.A_NORMAL)
            if surligner:
                attribut |= curses.A_REVERSE
            try:
                self.fenetre.addnstr(y, x, texte, largeur - 1 - x, attribut)
            except curses.error:
                pass  # la dernière case de l'écran refuse l'écriture : sans conséquence
            x += len(texte)

    # ------------------------------------------------------------ interactions ---

    def choisir(self, page: Page, liste: Liste, rafraichir=None, intervalle: float = 2.0):
        """Rend la valeur choisie (ou cochée), ou None si l'opérateur revient en
        arrière. `rafraichir(liste)` rend une nouvelle page et une nouvelle liste,
        appelé à intervalle régulier : le tableau des baies du mode station se met
        ainsi à jour tout seul (§9.4)."""
        dernier = time.monotonic()
        while True:
            entete = list(page.entete)
            if liste.explication:
                entete.append(Ligne.de(liste.explication, AIDE))
            self.dessiner(Page(page.titre, entete, page.aide), liste.lignes(),
                          liste.curseur, liste.message)
            touche = self.touche(0.5 if rafraichir else None)
            if touche is None or touche == "redim":
                if rafraichir and time.monotonic() - dernier >= intervalle:
                    page, liste = rafraichir(liste)
                    dernier = time.monotonic()
                continue
            action = liste.touche(touche)
            if action == VALIDER:
                return liste.choix
            if action == RETOUR:
                return None

    def saisir(self, page: Page, formulaire: Formulaire) -> dict | None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        while True:
            entete = list(page.entete)
            if formulaire.explication:
                entete.append(Ligne.de(formulaire.explication, AIDE))
            self.dessiner(Page(page.titre, entete, page.aide), formulaire.lignes(),
                          None, formulaire.message)
            touche = self.touche()
            if touche is None or touche == "redim":
                continue
            action = formulaire.touche(touche)
            if action == VALIDER:
                return formulaire.valeurs
            if action == RETOUR:
                return None

    def confirmer(self, page: Page, boutons: list[tuple[str, str]], touches: dict[str, str] | None = None):
        """L'écran de confirmation (§9.5) : des boutons, le premier — « Annuler » —
        choisi par défaut. Rend la clé du bouton validé, celle d'une touche
        spéciale, ou None."""
        liste = Liste("", [Element(libelle, cle) for cle, libelle in boutons])
        touches = touches or {}
        while True:
            self.dessiner(page, liste.lignes(), liste.curseur)
            touche = self.touche()
            if touche is None or touche == "redim":
                continue
            if touche.lower() in touches:
                return touches[touche.lower()]
            action = liste.touche(touche)
            if action == VALIDER:
                return liste.choix
            if action == RETOUR:
                return None

    def afficher(self, page: Page, lignes: list[Ligne]) -> None:
        """Un texte à lire, qu'on fait défiler ; Entrée ou Échap pour revenir.
        Rien ne s'efface tout seul (§9.7)."""
        haut = 0
        while True:
            hauteur, _ = self.fenetre.getmaxyx()
            visibles = lignes[haut:]
            self.dessiner(page, visibles)
            touche = self.touche()
            place = max(1, hauteur - len(page.entete) - 5)
            if touche == "bas" and haut + place < len(lignes):
                haut += 1
            elif touche == "haut" and haut > 0:
                haut -= 1
            elif touche in ("entree", "echap"):
                return

    def suivre(self, construire, en_cours, interrompre) -> None:
        """L'écran de progression (§9.6), redessiné deux fois par seconde tant
        que l'opération tourne. Échap propose de l'interrompre."""
        while en_cours():
            page, lignes = construire()
            self.dessiner(page, lignes)
            touche = self.touche(0.5)
            if touche == "echap":
                reponse = self.confirmer(
                    Page("Interrompre ?", [
                        Ligne.de("Les disques en cours d'écriture seront déclarés invalides.",
                                 AVERTISSEMENT),
                    ], "↑↓ : choisir   Entrée : valider"),
                    [("continuer", "Continuer l'opération"), ("interrompre", "Interrompre")],
                )
                if reponse == "interrompre":
                    interrompre()
        page, lignes = construire()
        self.dessiner(page, lignes)
