"""L'application : l'accueil, les trois opérations, le mode station, les
journaux (§9 de l'analyse).

Chaque opération suit les étapes décidées avec Kevin : choisir les disques,
confirmer — « Annuler » par défaut —, suivre la progression, lire le rapport.
Échap revient à l'étape précédente. Les disques qu'on ne peut pas choisir restent
visibles, grisés, avec leur motif ; ce qu'un disque peut devenir se décide dans
`devices`, jamais ici.
"""

from __future__ import annotations

import logging
import threading
import time

from .. import VERSION, config, demarrage, devices, filesystems, health, image, journal, layout, storage
from .. import texte
from ..engine import backup, clone, fanout, sources
from ..journal import Journal
from .model import (
    AIDE, AVERTISSEMENT, ECHEC, FORT, GRISE, NORMAL, OK,
    Champ, Element, Formulaire, Ligne, Liste, Page,
)

_log = logging.getLogger("clonegator.ui")

# Débits nominaux pour les estimations de l'écran de confirmation (§9.5) :
# lecture d'un disque source courant, relecture d'une sauvegarde compressée.
DEBIT_LECTURE = 150e6
DEBIT_SAUVEGARDE = {False: 100e6, True: 55e6}  # disque USB, partage réseau

AIDE_LISTE = "↑↓ ou numéro : choisir    Entrée : valider    Échap : retour"
AIDE_COCHER = ("↑↓ : se déplacer    Entrée, Espace ou numéro : cocher    "
               "« Valider », en bas, pour terminer    Échap : retour")
AIDE_FORMULAIRE = "Tapez le texte    Entrée : champ suivant, puis valider    Échap : retour"
AIDE_LIRE = "↑↓ : faire défiler    Entrée : revenir"


class ArretDemande(Exception):
    """Le système demande à CloneGator de s'arrêter : extinction de la
    machine, terminal perdu (SIGTERM, SIGHUP)."""


class Application:
    def __init__(self, ecran):
        self.ecran = ecran
        self.reglages = config.lire()
        self.arret_demande = False

    # -------------------------------------------------------------- accueil ---

    def lancer(self) -> None:
        try:
            if self.reglages.mode == config.MODE_STATION and self.reglages.station:
                if not self.station():
                    return
            self.accueil()
        except ArretDemande:
            pass  # une opération éventuelle a déjà été interrompue et rapportée

    def accueil(self) -> None:
        while True:
            liste = Liste("", [
                Element("Sauvegarder", "sauvegarder", detail="un disque vers une sauvegarde"),
                Element("Restaurer", "restaurer", detail="une sauvegarde vers un ou plusieurs disques"),
                Element("Cloner", "cloner", detail="un disque vers un ou plusieurs disques"),
                Element("Mode station", "station", detail="le même réglage à chaque fois, pour une machine à baies"),
                Element("Journaux", "journaux", detail="les dernières opérations"),
                Element("Quitter", "quitter"),
            ])
            choix = self.ecran.choisir(self._page("Accueil", aide=AIDE_LISTE), liste)
            if choix == "quitter":
                return
            if choix == "sauvegarder":
                self.sauvegarder()
            elif choix == "restaurer":
                self.restaurer()
            elif choix == "cloner":
                self.cloner()
            elif choix == "station":
                if self.configurer_station() and not self.station():
                    return
            elif choix == "journaux":
                self.journaux()

    # ------------------------------------------------------------ opérations ---

    def cloner(self, source: devices.Disque | None = None, cibles=None) -> None:
        """§9.3 : source → cibles → confirmation → progression → rapport. En mode
        station, source et cibles sont imposées : on arrive à la confirmation."""
        impose = source is not None
        etape = 2 if impose else 0
        while True:
            if etape == 0:
                source = self._choisir_source("Cloner — quel disque copier ?")
                if source is None:
                    return
                etape = 1
            elif etape == 1:
                cibles = self._choisir_cibles("Cloner — vers quels disques ?", source=source,
                                              precedentes=cibles)
                etape = 2 if cibles else 0
            else:
                plan = _plan_disque(source)
                decision = self._confirmer_clonage(source, cibles, plan)
                while decision == "brut":
                    plan = _plan_disque(source, brut=not plan.brut)
                    decision = self._confirmer_clonage(source, cibles, plan)
                if decision != "lancer":
                    if impose:
                        return
                    etape = 1
                    continue
                self._executer_clonage(sources.SourceDisque(source, brut=plan.brut), cibles,
                                       "clonage", plan.volume)
                return

    def sauvegarder(self, source: devices.Disque | None = None) -> None:
        """§9.3 : disque → où sont les sauvegardes → nom → confirmation → …"""
        impose = source is not None
        etape = 1 if impose else 0
        stockage = None
        nom = None
        try:
            while True:
                if etape == 0:
                    source = self._choisir_source("Sauvegarder — quel disque ?")
                    if source is None:
                        return
                    etape = 1
                elif etape == 1:
                    if stockage is not None:
                        storage.fermer(stockage)
                    stockage = self._choisir_stockage("Sauvegarder — où ranger la sauvegarde ?")
                    if stockage is None:
                        if impose:
                            return
                        etape = 0
                        continue
                    etape = 2
                elif etape == 2:
                    nom = self._saisir_nom(source, nom)
                    etape = 1 if nom is None else 3
                else:
                    plan = _plan_disque(source)
                    decision = self._confirmer_sauvegarde(source, stockage, nom, plan)
                    while decision == "brut":
                        plan = _plan_disque(source, brut=not plan.brut)
                        decision = self._confirmer_sauvegarde(source, stockage, nom, plan)
                    if decision != "lancer":
                        etape = 2
                        continue
                    self._executer_sauvegarde(source, stockage, nom, plan)
                    return
        finally:
            if stockage is not None:
                storage.fermer(stockage)

    def restaurer(self, cibles_imposees=None) -> None:
        """§9.3 : où sont les sauvegardes → la sauvegarde → cibles → confirmation → …"""
        stockage = None
        try:
            stockage = self._choisir_stockage("Restaurer — où sont les sauvegardes ?")
            if stockage is None:
                return
            while True:
                img = self._choisir_sauvegarde(stockage)
                if img is None:
                    return
                if cibles_imposees is not None:
                    cibles = self._cibles_station_pour(img.taille_requise, int(img.meta.get("secteur", 512)),
                                                       cibles_imposees)
                else:
                    cibles = self._choisir_cibles("Restaurer — vers quels disques ?", image=img)
                if not cibles:
                    continue
                if self._confirmer_restauration(img, stockage, cibles) != "lancer":
                    continue
                # Le volume décompressé, inscrit par la sauvegarde ; une image qui
                # ne l'a pas donne une progression sans pourcentage.
                volume = sum(int(p.get("volume", 0)) for p in img.partitions)
                self._executer_clonage(sources.SourceImage(img), cibles, "restauration", volume,
                                       reseau=stockage.reseau)
                return
        finally:
            if stockage is not None:
                storage.fermer(stockage)

    # --------------------------------------------------------- mode station ---

    def configurer_station(self) -> bool:
        """L'assistant du §3.3, pré-rempli avec le dernier réglage. Rend True si
        le mode station est activé."""
        precedent = self.reglages.station
        emplacements = _emplacements_internes()
        presents = {d.emplacement.cle: d for d in devices.inventaire() if d.emplacement}

        def element(e: devices.Emplacement) -> Element:
            disque = presents.get(e.cle)
            detail = (f"{disque.nom_noyau:<8} {disque.description}  {texte.taille(disque.taille)}"
                      if disque else "(vide)")
            return Element(e.nom, e.cle, detail=detail)

        etape = 0
        source = precedent.source if precedent else None
        cibles = list(precedent.cibles) if precedent else []
        while True:
            if etape == 0:
                liste = Liste("", [element(e) for e in emplacements],
                              explication="Sélectionnez l'emplacement source.")
                if source:
                    liste.placer(source)
                choix = self.ecran.choisir(self._page("Mode station — la source", aide=AIDE_LISTE), liste)
                if choix is None:
                    return False
                source = choix
                etape = 1
            elif etape == 1:
                autres = [e for e in emplacements if e.cle != source]
                liste = Liste("", [element(e) for e in autres], multiple=True,
                              explication="Sélectionnez les emplacements cibles. Leur contenu sera effacé à chaque clonage.")
                liste.cocher(cibles or [e.cle for e in autres])
                choix = self.ecran.choisir(self._page("Mode station — les cibles", aide=AIDE_COCHER), liste)
                if choix is None:
                    etape = 0
                    continue
                cibles = choix
                etape = 2
            else:
                liste = Liste("", [
                    Element("Oui", True, detail="la machine démarre directement sur le mode station"),
                    Element("Non", False, detail="on lance CloneGator soi-même"),
                ], explication="Voulez-vous que CloneGator démarre automatiquement en mode station "
                               "au démarrage de cet ordinateur ?")
                liste.placer(bool(precedent and precedent.lancement_auto))
                choix = self.ecran.choisir(
                    self._page("Mode station — lancement automatique", aide=AIDE_LISTE), liste)
                if choix is None:
                    etape = 1
                    continue
                self.reglages.station = config.ReglageStation(source, cibles, bool(choix))
                self.reglages.mode = config.MODE_STATION
                config.ecrire(self.reglages)
                motif = demarrage.activer() if choix else demarrage.desactiver()
                if motif:
                    self._message("Lancement automatique", [
                        Ligne.de("Le mode station est activé, mais pas son lancement automatique :", AVERTISSEMENT),
                        Ligne.de(motif)])
                return True

    def station(self) -> bool:
        """L'accueil du mode station (§9.4). Rend False pour quitter CloneGator,
        True pour revenir à l'accueil du mode libre."""
        while True:
            page, liste = self._accueil_station()
            choix = self.ecran.choisir(page, liste, rafraichir=lambda l: self._accueil_station(l))
            reglage = self.reglages.station
            if choix is None:
                continue
            source, cibles = _disques_station(reglage)
            if choix == "cloner":
                if source is None:
                    self._message("Cloner", [Ligne.de("Aucun disque dans l'emplacement source.", AVERTISSEMENT)])
                    continue
                cibles_ok = self._cibles_station_pour(_taille_requise(source), source.secteur_logique, cibles,
                                                      source=source)
                if not cibles_ok:
                    self._message("Cloner", [Ligne.de("Aucune cible prête.", AVERTISSEMENT)])
                    continue
                self.cloner(source, cibles_ok)
            elif choix == "restaurer":
                self.restaurer(cibles_imposees=cibles)
            elif choix == "sauvegarder":
                if source is None:
                    self._message("Sauvegarder", [Ligne.de("Aucun disque dans l'emplacement source.", AVERTISSEMENT)])
                    continue
                self.sauvegarder(source)
            elif choix == "quitter":
                self.reglages.mode = config.MODE_LIBRE
                config.ecrire(self.reglages)
                motif = demarrage.desactiver()
                if motif:
                    self._message("Lancement automatique", [Ligne.de(motif, AVERTISSEMENT)])
                return True

    def _accueil_station(self, precedente: Liste | None = None):
        reglage = self.reglages.station
        source, cibles = _disques_station(reglage)
        noms = {e.cle: e.nom for e in _emplacements_internes()}
        requis = _taille_requise(source) if source else None

        lignes = []
        def rangee(nom_emplacement, role, disque, etat, style):
            if disque is None:
                return Ligne([(f"  {nom_emplacement:<13} {role:<11} (vide)", GRISE)])
            return Ligne([
                (f"  {disque.libelle:<13} {role:<11} {disque.description[:20]:<20} "
                 f"{texte.taille(disque.taille):>9}   ", NORMAL),
                (etat, style)])

        if source is not None:
            refus = devices.refus_comme_source(source)
            lignes.append(rangee(noms.get(reglage.source, "?"), "SOURCE", source,
                                 refus or texte.contenu(source), AVERTISSEMENT if refus else NORMAL))
        else:
            lignes.append(rangee(noms.get(reglage.source, "?"), "SOURCE", None, "", NORMAL))
        for cle, disque in cibles:
            etat, style = "prêt", OK
            if disque is not None:
                refus = devices.refus_comme_cible(disque)
                sante = health.etat(disque)
                if refus:
                    etat, style = refus, AVERTISSEMENT
                elif requis is not None and disque.taille < requis:
                    etat, style = f"trop petit ({texte.taille(requis)} requis)", AVERTISSEMENT
                elif sante.niveau == health.USURE:
                    etat, style = f"prêt, {sante}", AVERTISSEMENT
            lignes.append(rangee(noms.get(cle, "?"), "CIBLE", disque, etat, style))

        for candidat in storage.candidats():
            libre = f"{texte.taille(candidat.libre)} libres" if candidat.libre is not None else "à monter"
            lignes.append(Ligne([(f"  {candidat.disque.libelle:<13} {'SAUVEGARDES':<11} "
                                  f"{candidat.disque.description[:20]:<20} {'':>9}   {libre}", AIDE)]))

        liste = Liste("", [
            Element("Cloner la source vers les cibles", "cloner"),
            Element("Restaurer une sauvegarde vers les cibles", "restaurer"),
            Element("Sauvegarder la source", "sauvegarder"),
            Element("Quitter le mode station", "quitter"),
        ])
        if precedente is not None:
            liste.curseur = precedente.curseur
        page = Page(f"CloneGator {VERSION} — mode station", lignes,
                    "↑↓ ou numéro : choisir    Entrée : valider")
        return page, liste

    def _cibles_station_pour(self, requis: int, secteur: int, cibles, source=None) -> list[devices.Disque]:
        """Les cibles du réglage prêtes pour cette source : présentes, admises par
        devices, assez grandes."""
        retenues = []
        for _, disque in cibles:
            if disque is None or (source is not None and disque.chemin == source.chemin):
                continue
            if devices.refus_comme_cible(disque) or disque.taille < requis:
                continue
            if disque.secteur_logique != secteur:
                continue
            retenues.append(disque)
        return retenues

    # ------------------------------------------------------------- journaux ---

    def journaux(self) -> None:
        while True:
            entrees = journal.lister()
            if not entrees:
                self._message("Journaux", [Ligne.de("Aucune opération enregistrée.")])
                return
            liste = Liste("", [
                Element(f"{e.date}  {e.operation:<14}", e, detail=_resume_rapport(e.rapport))
                for e in entrees
            ])
            choix = self.ecran.choisir(self._page("Journaux — les dernières opérations", aide=AIDE_LISTE), liste)
            if choix is None:
                return
            rapport = choix.rapport
            lignes = [Ligne.de(l) for l in rapport.splitlines()] if rapport else [
                Ligne.de("Pas de rapport pour cette opération (antérieure à l'interface).", GRISE),
                Ligne.de(""), Ligne.de(f"Journal : {choix.dossier}", AIDE)]
            self.ecran.afficher(self._page(f"Journal — {choix.date} {choix.operation}", aide=AIDE_LIRE), lignes)

    # --------------------------------------------------------------- étapes ---

    def _choisir_source(self, titre: str) -> devices.Disque | None:
        elements = []
        for disque in devices.inventaire():
            refus = devices.refus_comme_source(disque)
            elements.append(Element(f"{disque.libelle:<13} {disque.description:<22} "
                                    f"{texte.taille(disque.taille):>9}", disque,
                                    actif=not refus, motif=refus, detail=texte.contenu(disque)))
        if not elements:
            self._message(titre, [Ligne.de("Aucun disque détecté.", AVERTISSEMENT)])
            return None
        return self.ecran.choisir(self._page(titre, aide=AIDE_LISTE), Liste("", elements))

    def _choisir_cibles(self, titre: str, source: devices.Disque | None = None,
                        image=None, precedentes=None) -> list[devices.Disque] | None:
        if source is not None:
            requis, secteur = _taille_requise(source), source.secteur_logique
        else:
            requis, secteur = image.taille_requise, int(image.meta.get("secteur", 512))
        elements = []
        for disque in devices.inventaire():
            if source is not None and disque.chemin == source.chemin:
                continue
            motif = devices.refus_comme_cible(disque)
            if not motif and disque.taille < requis:
                motif = f"trop petit : {texte.taille(requis)} requis"
            if not motif and disque.secteur_logique != secteur:
                motif = f"secteurs de {disque.secteur_logique} octets, la source en a de {secteur}"
            smart = motif == devices.REFUS_SMART
            elements.append(Element(f"{disque.libelle:<13} {disque.description:<22} "
                                    f"{texte.taille(disque.taille):>9}", disque,
                                    actif=not motif, motif=motif, detail=texte.contenu(disque),
                                    forcable=smart,
                                    motif_force="! SMART défaillant, choisi quand même" if smart else ""))
        liste = Liste("", elements, multiple=True,
                      explication="Tout le contenu des disques cochés sera effacé.")
        aide = AIDE_COCHER
        if any(e.forcable for e in elements):
            aide += "    F : forcer un disque SMART défaillant"
        if precedentes:
            # Revenir de la confirmation ne doit pas faire tout recocher.
            chemins = {d.chemin for d in precedentes}
            liste.cocher([e.valeur for e in elements if e.valeur.chemin in chemins])
        return self.ecran.choisir(self._page(titre, aide=aide), liste)

    def _choisir_stockage(self, titre: str) -> storage.Stockage | None:
        """§7.4 : un disque USB ou le partage réseau ; l'étape apparaît toujours."""
        while True:
            candidats = storage.candidats()
            elements = []
            for candidat in candidats:
                libre = f"{texte.taille(candidat.libre)} libres" if candidat.libre is not None else "sera monté"
                elements.append(Element(candidat.nom, candidat, actif=candidat.utilisable,
                                        motif=candidat.refus, detail=libre))
            connexion = self.reglages.reseau
            libelle = (f"Partage réseau {connexion.unc}" if connexion.renseignee
                       else "Partage réseau Windows…")
            elements.append(Element(libelle, "reseau", detail="mot de passe demandé"))
            choix = self.ecran.choisir(self._page(titre, aide=AIDE_LISTE), Liste("", elements))
            if choix is None:
                return None
            if choix == "reseau":
                ouvert = self._ouvrir_partage()
                if ouvert is not None:
                    return ouvert
                continue
            try:
                return storage.ouvrir(choix)
            except storage.ErreurStockage as erreur:
                self._message(titre, [Ligne.de(f"{choix.nom} : {erreur}", AVERTISSEMENT)])

    def _ouvrir_partage(self) -> storage.Stockage | None:
        connexion = self.reglages.reseau
        formulaire = Formulaire("", [
            Champ("Hôte", connexion.hote),
            Champ("Partage", connexion.partage),
            Champ("Utilisateur", connexion.utilisateur),
            Champ("Mot de passe", "", masque=True),
        ], explication="Partage Windows : \\\\hôte\\partage. Le mot de passe n'est jamais enregistré.")
        formulaire.curseur = 3 if connexion.renseignee else 0
        while True:
            valeurs = self.ecran.saisir(self._page("Partage réseau", aide=AIDE_FORMULAIRE), formulaire)
            if valeurs is None:
                return None
            essai = config.ConnexionReseau(valeurs["Hôte"].strip().strip("\\/"),
                                           valeurs["Partage"].strip().strip("\\/"),
                                           valeurs["Utilisateur"].strip())
            self.ecran.dessiner(self._page("Partage réseau"), [Ligne.de(f"Connexion à {essai.unc}…")])
            try:
                ouvert = storage.ouvrir(storage.partage(essai), valeurs["Mot de passe"])
            except storage.ErreurStockage as erreur:
                formulaire.message = str(erreur)
                formulaire.champs[3].valeur = ""
                continue
            finally:
                valeurs["Mot de passe"] = ""
            # Hôte, partage et utilisateur sont mémorisés ; le mot de passe, jamais.
            self.reglages.reseau = essai
            config.ecrire(self.reglages)
            return ouvert

    def _choisir_sauvegarde(self, stockage: storage.Stockage):
        sauvegardes = list(reversed(image.lister(stockage.racine)))  # la plus récente en haut
        if not sauvegardes:
            self._message("Restaurer", [Ligne.de(f"Aucune sauvegarde sur {stockage.nom}.", AVERTISSEMENT)])
            return None
        elements = [
            Element(f"{img.etiquette:<24} {img.meta.get('date', '?'):<17}", img,
                    detail=f"{img.origine.get('modele', '?')}, {texte.taille(int(img.origine.get('taille', 0)))}"
                           f" — sauvegarde de {texte.taille(img.taille_sur_disque)}"
                           + ("  (copie intégrale)" if img.mode == image.MODE_BRUT else ""))
            for img in sauvegardes
        ]
        return self.ecran.choisir(self._page("Restaurer — quelle sauvegarde ?", aide=AIDE_LISTE),
                                  Liste("", elements))

    def _saisir_nom(self, source: devices.Disque, precedent: str | None = None) -> str | None:
        propose = precedent or "".join(
            c if c.isalnum() or c in "-_" else "-" for c in source.description).strip("-")
        formulaire = Formulaire("", [Champ("Nom de la sauvegarde", propose or "sauvegarde")],
                                explication="Pour la reconnaître dans la liste de restauration, ex. Win11-labo-info.")
        while True:
            valeurs = self.ecran.saisir(self._page("Sauvegarder — nom de la sauvegarde", aide=AIDE_FORMULAIRE),
                                        formulaire)
            if valeurs is None:
                return None
            nom = valeurs["Nom de la sauvegarde"].strip()
            if nom:
                return nom
            formulaire.message = "Donnez un nom à la sauvegarde."

    # --------------------------------------------------------- confirmations ---

    def _confirmer_clonage(self, source, cibles, plan) -> str | None:
        lignes = [_ligne_disque("Source", source), Ligne.de("")]
        lignes.append(Ligne.de("Cibles — TOUT LEUR CONTENU SERA EFFACÉ :", AVERTISSEMENT))
        lignes += [_ligne_disque("  ", c) for c in cibles]
        lignes += [Ligne.de("")] + plan.lignes(DEBIT_LECTURE)
        return self.ecran.confirmer(
            self._page("Cloner — confirmation", lignes,
                       aide="↑↓ : choisir    Entrée : valider    I : copie intégrale    Échap : annuler"),
            [("annuler", "Annuler"), ("lancer", f"Lancer le clonage vers {len(cibles)} disque(s)")],
            {"i": "brut"})

    def _confirmer_sauvegarde(self, source, stockage, nom, plan) -> str | None:
        lignes = [_ligne_disque("Source", source),
                  Ligne.de(f"Vers    {stockage.nom}, {texte.taille(stockage.libre)} libres"),
                  Ligne.de(f"Nom     {nom}"), Ligne.de("")]
        lignes += plan.lignes(DEBIT_LECTURE)
        if stockage.libre is not None and stockage.libre < plan.volume:
            lignes.append(Ligne.de("! Espace libre inférieur au volume à lire : la compression le "
                                   "réduit souvent assez, sans garantie (§7.5).", AVERTISSEMENT))
        return self.ecran.confirmer(
            self._page("Sauvegarder — confirmation", lignes,
                       aide="↑↓ : choisir    Entrée : valider    I : copie intégrale    Échap : annuler"),
            [("annuler", "Annuler"), ("lancer", "Lancer la sauvegarde")],
            {"i": "brut"})

    def _confirmer_restauration(self, img, stockage, cibles) -> str | None:
        debit = DEBIT_SAUVEGARDE[stockage.reseau]
        volume = img.taille_sur_disque
        lignes = [Ligne.de(f"Sauvegarde  {img.etiquette}, du {img.meta.get('date', '?')}, "
                           f"d'un {img.origine.get('modele', '?')}"),
                  Ligne.de(f"            sur {stockage.nom}"), Ligne.de("")]
        lignes.append(Ligne.de("Cibles — TOUT LEUR CONTENU SERA EFFACÉ :", AVERTISSEMENT))
        lignes += [_ligne_disque("  ", c) for c in cibles]
        lignes += [Ligne.de(""),
                   Ligne.de(f"La sauvegarde ({texte.taille(volume)}) est d'abord vérifiée, puis copiée : "
                            f"environ {texte.duree(2 * volume / debit)} en tout.")]
        return self.ecran.confirmer(
            self._page("Restaurer — confirmation", lignes, aide="↑↓ : choisir    Entrée : valider    Échap : annuler"),
            [("annuler", "Annuler"), ("lancer", f"Lancer la restauration vers {len(cibles)} disque(s)")])

    # -------------------------------------------------------------- exécution ---

    def _executer_clonage(self, source, cibles, nom_operation: str, volume: int, reseau: bool = False) -> None:
        # Une cible choisie malgré un SMART défaillant l'a été délibérément (touche F).
        forcees = frozenset(c.chemin for c in cibles if health.etat(c).niveau == health.DEFAILLANT)
        with Journal(nom_operation) as j:
            operation = clone.Clonage(source, cibles, j, forcer_smart=forcees)
            titre = "Clonage" if nom_operation == "clonage" else "Restauration"
            suivi = _Suivi(operation, volume)
            self._executer(operation, lambda: suivi.page(titre))
            lignes = _rapport_clonage(operation, j, titre)
            j.ecrire_rapport("\n".join(l.texte() for l in lignes))
        if self.arret_demande:
            raise ArretDemande()
        self.ecran.afficher(self._page(f"{titre} — rapport", aide="Entrée : revenir"), lignes)

    def _executer_sauvegarde(self, source, stockage, nom, plan) -> None:
        with Journal("sauvegarde") as j:
            operation = backup.Sauvegarde(source, stockage.racine, nom, j, brut=plan.brut)
            suivi = _Suivi(operation, plan.volume)
            self._executer(operation, lambda: suivi.page("Sauvegarde"))
            lignes = _rapport_sauvegarde(operation, j, stockage)
            j.ecrire_rapport("\n".join(l.texte() for l in lignes))
        if self.arret_demande:
            raise ArretDemande()
        self.ecran.afficher(self._page("Sauvegarde — rapport", aide="Entrée : revenir"), lignes)

    def _executer(self, operation, construire) -> None:
        erreurs = []

        def tourner():
            try:
                operation.executer()
            except BaseException as erreur:  # déjà consignée par l'opération
                erreurs.append(erreur)

        fil = threading.Thread(target=tourner, name="operation")
        fil.start()
        try:
            self.ecran.suivre(construire, fil.is_alive, operation.arreter)
        except ArretDemande:
            # §13 : une coupure arrête proprement les écritures ; les cibles
            # sont déclarées interrompues, et le rapport est quand même écrit.
            self.arret_demande = True
            operation.arreter()
        fil.join()

    # ---------------------------------------------------------------- outils ---

    def _page(self, titre: str, entete=None, aide: str = "") -> Page:
        mode = " — mode station" if self.reglages.mode == config.MODE_STATION else ""
        return Page(f"CloneGator {VERSION}{mode} — {titre}", entete or [], aide)

    def _message(self, titre: str, lignes: list[Ligne]) -> None:
        self.ecran.afficher(self._page(titre, aide="Entrée : revenir"), lignes)


# ---------------------------------------------------------------- calculs ---

class _Plan:
    """Ce que copiera une source disque, pour l'écran de confirmation (§9.5)."""

    def __init__(self, disque: devices.Disque, brut: bool):
        self.disque = disque
        self.brut = brut
        self.volume = 0
        self.notes: list[Ligne] = []
        if brut:
            self.volume = disque.taille
            return
        partitions = {p.numero: p for p in disque.partitions}
        table = layout.lire(disque.chemin)
        for entree in table.entrees:
            partition = partitions.get(entree.numero)
            if partition is None:
                continue
            choix = filesystems.choisir(partition, entree.etendue)
            volume = filesystems.volume_a_copier(partition, choix)
            self.volume += volume
            if choix.avertissement:
                self.notes.append(Ligne.de(
                    f"! partition {entree.numero} : {choix.raison} — {texte.taille(volume)}, "
                    f"environ {texte.duree(volume / DEBIT_LECTURE)} à elle seule", AVERTISSEMENT))

    def lignes(self, debit: float) -> list[Ligne]:
        if self.brut:
            return [Ligne.de(f"Copie intégrale du disque, secteur par secteur : {texte.taille(self.volume)}, "
                             f"environ {texte.duree(self.volume / debit)}. LENT.", AVERTISSEMENT)]
        return [Ligne.de(f"À copier : {texte.taille(self.volume)}, environ "
                         f"{texte.duree(self.volume / debit)}.")] + self.notes


def _plan_disque(disque: devices.Disque, brut: bool | None = None) -> _Plan:
    """Le plan de copie ; une table non reconnue impose la copie intégrale (§9.3)."""
    if brut is None:
        try:
            return _Plan(disque, brut=False)
        except layout.ErreurTable:
            return _Plan(disque, brut=True)
    try:
        return _Plan(disque, brut=brut)
    except layout.ErreurTable:
        return _Plan(disque, brut=True)


def _taille_requise(disque: devices.Disque) -> int:
    try:
        return layout.lire(disque.chemin).taille_requise
    except layout.ErreurTable:
        return disque.taille


def _emplacements_internes() -> list[devices.Emplacement]:
    emplacements = list(devices.emplacements_sata())
    for disque in devices.inventaire():
        e = disque.emplacement
        if e and devices.admis_en_station(e) and e not in emplacements:
            emplacements.append(e)
    return emplacements


def _disques_station(reglage: config.ReglageStation):
    presents = {d.emplacement.cle: d for d in devices.inventaire() if d.emplacement}
    return presents.get(reglage.source), [(cle, presents.get(cle)) for cle in reglage.cibles]


class _Suivi:
    """Les chiffres de l'écran de progression, cumulés d'une partition à l'autre."""

    def __init__(self, operation, volume: int):
        self.operation = operation
        self.volume = volume
        self.debut = time.monotonic()
        self._terminees = 0
        self._courante = None

    def page(self, titre: str):
        operation = self.operation
        diffusion = operation.diffusion
        if diffusion is not self._courante:
            if self._courante is not None:
                self._terminees += self._courante.octets_lus
            self._courante = diffusion
        lu = self._terminees + (diffusion.octets_lus if diffusion else 0)
        ecoule = time.monotonic() - self.debut

        lignes = [Ligne.de(f"Étape    {operation.etape}"),
                  Ligne.de(f"Écoulé   {texte.duree(ecoule)}")]
        if isinstance(operation, backup.Sauvegarde):
            lignes.append(Ligne.de(f"Écrit    {texte.taille(lu)} compressés"))
        elif self.volume:
            part = min(100, 100 * lu / self.volume)
            reste = ""
            if lu and part < 100:
                reste = f" — reste environ {texte.duree(ecoule * (self.volume - lu) / lu)}"
            lignes.append(Ligne.de(f"Lu       {texte.taille(lu)} sur ~{texte.taille(self.volume)} "
                                   f"({part:.0f} %){reste}"))
        lignes.append(Ligne.de(""))

        debits = {c.nom: c for c in diffusion.cibles} if diffusion else {}
        if isinstance(operation, clone.Clonage):
            for cible in operation.cibles:
                suivi = debits.get(cible.nom)
                if cible.etat == fanout.EN_COURS:
                    etat = f"en cours   {texte.debit(suivi.debit)}" if suivi and suivi.active else "en cours"
                    style = NORMAL
                elif cible.etat == clone.REUSSIE:
                    etat, style = "RÉUSSIE", OK
                elif cible.etat == clone.EN_ATTENTE:
                    etat, style = "en attente", GRISE
                else:
                    etat = cible.etat.upper() + (f" — {cible.motif}" if cible.motif else "")
                    style = ECHEC
                lignes.append(Ligne([(f"  {cible.disque.libelle:<14} ", FORT), (etat, style)]))
        elif diffusion:
            suivi = diffusion.cibles[0]
            lignes.append(Ligne.de(f"  {suivi.nom}   {texte.debit(suivi.debit)}"))
        return (Page(f"CloneGator {VERSION} — {titre} en cours", [], "Échap : interrompre"), lignes)


def _ligne_disque(prefixe: str, disque: devices.Disque) -> Ligne:
    return Ligne([(f"{prefixe:<8}", NORMAL), (f"{disque.libelle:<14}", FORT),
                  (f" {disque.description}, s/n {disque.serie or '?'}, {texte.taille(disque.taille)}", NORMAL)])


def _rapport_clonage(operation: clone.Clonage, j: Journal, titre: str) -> list[Ligne]:
    reussies = sum(1 for c in operation.cibles if c.etat == clone.REUSSIE)
    lignes = [Ligne.de(f"{titre} du {time.strftime('%Y-%m-%d %H:%M')} — {reussies} réussie(s) "
                       f"sur {len(operation.cibles)}", FORT),
              Ligne.de(f"Source : {operation.source.description}"),
              Ligne.de(f"Durée totale : {texte.duree(operation.duree)}"), Ligne.de("")]
    for cible in operation.cibles:
        style = OK if cible.etat == clone.REUSSIE else ECHEC
        lignes.append(Ligne([(f"  {cible.disque.libelle:<14} s/n {cible.disque.serie or '?':<18} ", NORMAL),
                             (cible.etat.upper(), style),
                             ((f" — {cible.motif}" if cible.motif else ""), NORMAL)]))
        for avertissement in cible.avertissements:
            lignes.append(Ligne.de(f"      ! {avertissement}", AVERTISSEMENT))
    lignes += [Ligne.de(""), Ligne.de(f"Journal : {j.dossier}", AIDE)]
    return lignes


def _rapport_sauvegarde(operation: backup.Sauvegarde, j: Journal, stockage) -> list[Ligne]:
    reussie = operation.etat == backup.REUSSIE
    lignes = [Ligne.de(f"Sauvegarde du {time.strftime('%Y-%m-%d %H:%M')} — "
                       f"{'RÉUSSIE' if reussie else operation.etat.upper()}", OK if reussie else ECHEC),
              Ligne.de(f"Source : {operation.source.description}"),
              Ligne.de(f"Vers : {stockage.nom}"),
              Ligne.de(f"Durée totale : {texte.duree(operation.duree)}"), Ligne.de("")]
    if reussie:
        img = image.lire(operation.dossier)
        lignes.append(Ligne.de(f"Sauvegarde « {img.etiquette} » : {texte.taille(img.taille_sur_disque)}"))
        lignes.append(Ligne.de(f"Dossier : {storage.chemin_affiche(stockage, operation.dossier)}", AIDE))
    else:
        lignes.append(Ligne.de(f"Motif : {operation.motif}", ECHEC))
        lignes.append(Ligne.de("Le dossier reste incomplet : il ne sera jamais proposé à la restauration.", AIDE))
    for avertissement in operation.avertissements:
        lignes.append(Ligne.de(f"! {avertissement}", AVERTISSEMENT))
    lignes += [Ligne.de(""), Ligne.de(f"Journal : {j.dossier}", AIDE)]
    return lignes


def _resume_rapport(rapport: str | None) -> str:
    if not rapport:
        return ""
    premiere = rapport.splitlines()[0]
    return premiere.split(" — ", 1)[1] if " — " in premiere else premiere


def demarrer() -> int:
    """Ouvre l'interface sur le terminal courant."""
    import curses
    import locale
    import os
    import signal

    from .. import verrou
    from .ecran import Ecran

    tenu = verrou.prendre()
    if tenu is None:
        print("CloneGator est déjà ouvert sur un autre écran de cette machine.")
        return 1
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")  # Échap répond tout de suite

    def arreter(_signal, _cadre):
        raise ArretDemande()

    # Extinction de la machine, terminal perdu : arrêt propre, jamais brutal.
    signal.signal(signal.SIGTERM, arreter)
    signal.signal(signal.SIGHUP, arreter)

    def principal(fenetre):
        curses.raw()  # Ctrl-C devient une touche : l'interface ne meurt pas en pleine copie
        Application(Ecran(fenetre)).lancer()

    try:
        curses.wrapper(principal)
    except ArretDemande:
        pass  # demandé hors d'une page : rien à interrompre
    finally:
        tenu.close()
    return 0
