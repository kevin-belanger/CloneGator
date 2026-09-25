"""Lancement automatique au démarrage, en mode station (§3.3 de l'analyse).

Une unité systemd ouvre CloneGator sur la console principale (tty1), à la place
de l'invite de connexion. Quand on quitte CloneGator, l'invite de connexion
reprend la console : la machine n'est jamais laissée sans rien à l'écran.

CloneGator active et désactive lui-même ce lancement, depuis l'assistant du mode
station ; rien ne se lance automatiquement en dehors de ce mode.
"""

from __future__ import annotations

import logging
import os
import sys

from . import sysexec

_log = logging.getLogger("clonegator.demarrage")

UNITE = "clonegator.service"
FICHIER_UNITE = f"/etc/systemd/system/{UNITE}"
GETTY = "getty@tty1.service"
INSTALLE = "/usr/bin/clonegator"


def _contenu() -> str:
    # Installé par le paquet, CloneGator se lance par sa commande ; en
    # développement, on lance celui du dépôt, là où il est.
    dossier = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    commande = (INSTALLE if dossier == "/usr/lib/clonegator" and os.path.exists(INSTALLE)
                else f"{sys.executable} -m clonegator")
    return f"""[Unit]
Description=CloneGator en mode station, sur la console
# Démarrer après l'arrêt de l'invite de connexion : sa session, en se fermant,
# raccrocherait sinon le terminal sous le nez de CloneGator (SIGHUP).
After=systemd-user-sessions.service {GETTY}
Conflicts={GETTY}

[Service]
Type=simple
WorkingDirectory={dossier}
ExecStart={commande}
# En quittant CloneGator, rendre la console à l'invite de connexion.
ExecStopPost=/bin/systemctl --no-block start {GETTY}
StandardInput=tty-force
StandardOutput=tty
StandardError=journal
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
Environment=TERM=linux LANG=C.UTF-8
Restart=no

[Install]
WantedBy=multi-user.target
"""


def en_live() -> bool:
    """CloneGator tourne-t-il depuis le live (§15) ? Il y démarre d'office, et
    rien de ce qu'il écrit ne survit au redémarrage : un lancement automatique
    n'y a pas de sens."""
    return "boot=live" in (sysexec.lire("/proc/cmdline") or "").split()


def actif() -> bool:
    return sysexec.executer(["systemctl", "is-enabled", "--quiet", UNITE]).ok


def activer() -> str:
    """Installe l'unité et l'active pour les prochains démarrages. Rend un
    motif d'échec, ou une chaîne vide."""
    try:
        with open(FICHIER_UNITE, "w", encoding="utf-8") as fichier:
            fichier.write(_contenu())
    except OSError as erreur:
        return f"unité systemd non écrite : {erreur.strerror}"
    for argv in (["systemctl", "daemon-reload"],
                 ["systemctl", "disable", GETTY],
                 ["systemctl", "enable", UNITE]):
        resultat = sysexec.executer(argv)
        if not resultat.ok:
            return f"{' '.join(argv)} a échoué : {resultat.erreur.strip()}"
    _log.info("lancement automatique activé")
    return ""


def desactiver() -> str:
    """Rend la console à l'invite de connexion aux prochains démarrages."""
    if not os.path.exists(FICHIER_UNITE):
        return ""
    for argv in (["systemctl", "disable", UNITE],
                 ["systemctl", "enable", GETTY]):
        resultat = sysexec.executer(argv)
        if not resultat.ok:
            return f"{' '.join(argv)} a échoué : {resultat.erreur.strip()}"
    _log.info("lancement automatique désactivé")
    return ""
