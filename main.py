#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Launcher — lance BotJanus (license_bot.py) et le bot de nettoyage d'images
supprimées (image_cleanup_bot.py) en parallèle, dans le même processus.

Aucune logique des deux bots n'est modifiée : chacun garde sa propre
boucle de sondage (POLL_INTERVAL, DRY_RUN, fichiers d'état, etc.),
son propre logger, et son propre appel à pywikibot.Site()/login().

pywikibot met en cache les objets Site par (famille, langue), donc les
deux bots partagent en réalité la même session/throttle vers l'API —
pas de double authentification ni de double débit involontaire.

Usage :
    python launcher.py
    (Ctrl+C pour arrêter les deux)
"""

import sys
import time
import logging
import threading

import pywikibot
import license_bot
import image_cleanup_bot

# Initialisation du site par défaut pour la session Pywikibot globale
site = pywikibot.Site("fr", "vikidia")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("launcher")


def run_bot(name, entry_point):
    """Encapsule main() de chaque bot pour logger proprement les crashs
    sans faire tomber l'autre bot ni le launcher."""
    log.info("Démarrage du thread : %s", name)
    try:
        entry_point()
    except Exception:
        log.exception("Le bot '%s' s'est arrêté suite à une exception non gérée.", name)


def main():
    threads = [
        threading.Thread(
            target=run_bot,
            args=("license_bot", license_bot.main),
            name="license_bot",
            daemon=True,
        ),
        threading.Thread(
            target=run_bot,
            args=("image_cleanup_bot", image_cleanup_bot.main),
            name="image_cleanup_bot",
            daemon=True,
        ),
    ]

    for t in threads:
        t.start()
        time.sleep(2)  # léger décalage pour éviter deux logins simultanés

    log.info("Les deux bots tournent. Ctrl+C pour arrêter.")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("Arrêt demandé (Ctrl+C). Les threads sont daemon, le processus va se terminer.")
        sys.exit(0)


if __name__ == "__main__":
    main()
