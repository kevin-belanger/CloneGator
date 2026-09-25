"""Format d'image (§7.2 de l'analyse).

Une image est un dossier dans `CloneGator/`, à la racine du disque de
stockage. Elle doit se comprendre sans CloneGator : un fichier par partition,
nommé d'après son numéro et son contenu, une table en texte, des empreintes au
format de `sha256sum`, et un `LISEZMOI.txt` qui explique comment la restaurer
à la main.

**Une image est complète quand `clonegator.json` existe** : il est écrit en
tout dernier, d'un seul coup (écriture dans un fichier temporaire, puis
renommage). Une sauvegarde interrompue laisse un dossier sans lui, qui
n'apparaît jamais dans la liste.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass

from . import VERSION, sysexec

_log = logging.getLogger("clonegator.image")

FORMAT = 1

RACINE = "CloneGator"
METADONNEES = "clonegator.json"
LISEZMOI = "LISEZMOI.txt"
TETE = "disque.tete"
TABLE = "disque.sfdisk"
DISQUE_BRUT = "disque.brut.zst"
EMPREINTES = "empreintes.sha256"
JOURNAL = "clonegator.log"

MODE_AUTO = "auto"
MODE_BRUT = "brut"


class ErreurImage(Exception):
    pass


def fichier_partition(numero: int, moteur: str, fstype: str | None) -> str:
    """`p3.ntfs.pcl.zst` pour une image partclone, `p2.brut.zst` pour une copie brute."""
    if moteur == "partclone":
        return f"p{numero}.{fstype}.pcl.zst"
    return f"p{numero}.brut.zst"


def fichier_secours_ntfs(numero: int) -> str:
    return f"p{numero}.ntfs-secours"


def nom_dossier(etiquette: str, quand: float | None = None) -> str:
    """`2026-09-24_1430_Win11-labo` : la date d'abord, pour que l'ordre
    alphabétique soit l'ordre chronologique."""
    horodatage = time.strftime("%Y-%m-%d_%H%M", time.localtime(quand))
    propre = "".join(c if c.isalnum() or c in "-_" else "-" for c in etiquette.strip())
    return f"{horodatage}_{propre.strip('-') or 'image'}"


@dataclass
class Image:
    dossier: str
    meta: dict

    @property
    def nom(self) -> str:
        return os.path.basename(self.dossier)

    @property
    def etiquette(self) -> str:
        return self.meta.get("etiquette", "")

    @property
    def mode(self) -> str:
        return self.meta.get("mode", MODE_AUTO)

    @property
    def taille_requise(self) -> int:
        return int(self.meta["taille_requise"])

    @property
    def origine(self) -> dict:
        return self.meta.get("disque_origine", {})

    @property
    def partitions(self) -> list[dict]:
        return self.meta.get("partitions", [])

    def chemin(self, fichier: str) -> str:
        return os.path.join(self.dossier, fichier)

    @property
    def taille_sur_disque(self) -> int:
        total = 0
        for nom in os.listdir(self.dossier):
            chemin = self.chemin(nom)
            if os.path.isfile(chemin):
                total += os.path.getsize(chemin)
        return total


def lire(dossier: str) -> Image:
    """Une image complète. Lève ErreurImage sinon."""
    chemin = os.path.join(dossier, METADONNEES)
    try:
        with open(chemin, encoding="utf-8") as fichier:
            meta = json.load(fichier)
    except FileNotFoundError:
        raise ErreurImage(f"{os.path.basename(dossier)} : image incomplète (pas de {METADONNEES})")
    except (OSError, ValueError) as erreur:
        raise ErreurImage(f"{os.path.basename(dossier)} : {METADONNEES} illisible ({erreur})")

    if not isinstance(meta, dict) or "taille_requise" not in meta:
        raise ErreurImage(f"{os.path.basename(dossier)} : {METADONNEES} incomplet")
    if int(meta.get("format", 0)) > FORMAT:
        raise ErreurImage(
            f"{os.path.basename(dossier)} : format {meta.get('format')} plus récent que ce "
            f"CloneGator (format {FORMAT}) ; mettre CloneGator à jour"
        )
    return Image(dossier, meta)


def lister(racine_stockage: str) -> list[Image]:
    """Les images complètes du disque de stockage, de la plus ancienne à la plus
    récente. Un dossier sans `clonegator.json` est ignoré, en silence dans la
    liste mais pas dans le journal."""
    racine = os.path.join(racine_stockage, RACINE)
    try:
        noms = sorted(os.listdir(racine))
    except FileNotFoundError:
        return []
    images = []
    for nom in noms:
        dossier = os.path.join(racine, nom)
        if not os.path.isdir(dossier):
            continue
        try:
            images.append(lire(dossier))
        except ErreurImage as erreur:
            _log.info("ignoré : %s", erreur)
    return images


def verifier_empreintes(image: Image, suivre=None) -> str:
    """Motif du refus si un fichier de l'image ne correspond pas à son empreinte,
    chaîne vide sinon. C'est `sha256sum -c`, exactement ce qu'un humain ferait.

    `suivre(processus)` inscrit le programme auprès de l'opération : la
    vérification relit des Go, parfois par le réseau, et une interruption doit
    pouvoir l'arrêter sans attendre la fin.
    """
    processus = sysexec.Processus(
        ["sha256sum", "--check", "--quiet", "--strict", EMPREINTES],
        flux_sortant=True, dossier=image.dossier,
    )
    if suivre is not None:
        suivre(processus)
    morceaux = []
    while morceau := os.read(processus.sortie, 65536):  # quelques lignes au plus
        morceaux.append(morceau)
    resultat = processus.attendre(60)
    if resultat.ok:
        return ""
    defauts = [l for l in b"".join(morceaux).decode(errors="replace").splitlines() if l.strip()]
    return "image altérée — " + ("; ".join(defauts[:3]) or resultat.erreur.strip() or "vérification impossible")


def empreinte_fichier(chemin: str) -> str:
    hachage = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        for morceau in iter(lambda: fichier.read(4 * 1024 * 1024), b""):
            hachage.update(morceau)
    return hachage.hexdigest()


def ecrire_empreintes(dossier: str, empreintes: dict[str, str]) -> None:
    lignes = "".join(f"{empreinte}  {nom}\n" for nom, empreinte in empreintes.items())
    _ecrire(os.path.join(dossier, EMPREINTES), lignes)


def ecrire_metadonnees(dossier: str, meta: dict) -> None:
    """Le dernier geste d'une sauvegarde : après lui, l'image existe."""
    meta = {"format": FORMAT, "clonegator": VERSION, **meta}
    temporaire = os.path.join(dossier, f".{METADONNEES}.tmp")
    _ecrire(temporaire, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporaire, os.path.join(dossier, METADONNEES))
    _synchroniser_dossier(dossier)


def ecrire_lisezmoi(dossier: str, meta: dict) -> None:
    _ecrire(os.path.join(dossier, LISEZMOI), _lisezmoi(meta))


def _lisezmoi(meta: dict) -> str:
    origine = meta.get("disque_origine", {})
    taille = _go(int(meta["taille_requise"]))
    lignes = [
        f"Image CloneGator « {meta.get('etiquette', '')} », du {meta.get('date', '?')}.",
        f"Disque d'origine : {origine.get('modele', '?')}, numéro de série "
        f"{origine.get('serie') or '?'}, {_go(int(origine.get('taille', 0)))}.",
        "",
        "Le plus simple pour la restaurer est CloneGator. Sans lui, sur n'importe quel",
        "Linux où sont installés partclone, zstd et util-linux, vers un disque",
        f"/dev/sdX d'au moins {taille} — TOUT SON CONTENU SERA EFFACÉ :",
        "",
        "1. Vérifier que l'image est intacte :",
        f"     sha256sum -c {EMPREINTES}",
        "",
    ]
    if meta.get("mode") == MODE_BRUT:
        lignes += [
            "2. Réécrire le disque entier :",
            f"     zstd -dc {DISQUE_BRUT} | dd of=/dev/sdX bs=4M conv=fsync",
        ]
        return "\n".join(lignes) + "\n"

    lignes += [
        "2. Tête du disque, puis table de partitions :",
        f"     dd if={TETE} of=/dev/sdX conv=fsync",
        f"     grep -v -e '^device:' -e '^last-lba:' {TABLE} \\",
        "       | sfdisk --wipe always --wipe-partitions always /dev/sdX",
        "",
        "3. Chaque partition. Le nom de la partition N est /dev/sdXN, ou /dev/nvme0n1pN",
        "   pour un disque NVMe :",
    ]
    for partition in meta.get("partitions", []):
        numero = partition["numero"]
        cible = f"/dev/sdX{numero}"
        moteur = partition["moteur"]
        if moteur == "partclone":
            lignes.append(f"     zstd -dc {partition['fichier']} | "
                          f"{partition['programme']} -r -s - -o {cible}")
        elif moteur == "brut":
            lignes.append(f"     zstd -dc {partition['fichier']} | dd of={cible} bs=4M conv=fsync")
        elif moteur == "swap":
            options = ""
            if partition.get("uuid"):
                options += f" -U {partition['uuid']}"
            if partition.get("etiquette"):
                options += f" -L {partition['etiquette']}"
            lignes.append(f"     mkswap{options} {cible}")
        secours = partition.get("secours")
        if secours:
            secteur = int(meta.get("secteur", 512))
            lignes.append(f"     dd if={secours['fichier']} of={cible} bs={secteur} "
                          f"seek={secours['position'] // secteur} conv=notrunc,fsync")
    etendues = [p["numero"] for p in meta.get("partitions", []) if p["moteur"] == "aucun"]
    if etendues:
        lignes += [
            "",
            f"   La partition {', '.join(map(str, etendues))} est une partition étendue : elle n'a",
            "   pas de fichier, la table la décrit.",
        ]
    return "\n".join(lignes) + "\n"


def _go(octets: int) -> str:
    return f"{octets / 1e9:.1f} Go".replace(".", ",")


def _ecrire(chemin: str, texte: str) -> None:
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write(texte)
        fichier.flush()
        os.fsync(fichier.fileno())


def _synchroniser_dossier(dossier: str) -> None:
    fd = os.open(dossier, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
