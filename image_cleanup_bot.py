#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de nettoyage des images supprimées sur Vikidia (fr.vikidia.org)
=====================================================================

Ce script :
1. Surveille en boucle le journal de suppression (Recent Changes) pour
   détecter les suppressions de fichiers/images (namespace 6 = Fichier).
2. Pour chaque image supprimée, récupère la liste des pages qui la
   référencent via l'API `backlinks` (fonctionne même après suppression,
   car la table pagelinks n'est pas effacée immédiatement).
3. Supprime la syntaxe [[Fichier:...]] / [[File:...]] correspondante
   dans le wikicode de ces pages et sauvegarde la modification.

Dépendances : pywikibot
    pip install pywikibot --break-system-packages

Configuration nécessaire avant utilisation :
    - Un compte bot sur Vikidia avec le flag "bot" (ou au moins autorisé
      à éditer en automatique - vérifier la politique du wiki avant de
      lancer en production).
    - user-config.py avec la famille "vikidia" et la langue "fr"
      (voir bloc VIKIDIA_FAMILY_STUB ci-dessous si pywikibot ne connaît
      pas nativement Vikidia).

⚠️ IMPORTANT : teste d'abord avec DRY_RUN = True pour vérifier ce que
le bot ferait, sans réellement sauvegarder les pages.
"""

import re
import sys
import time
import logging
from datetime import datetime, timedelta, timezone

import pywikibot
from pywikibot import pagegenerators

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

LANG = "fr"
FAMILY = "vikidia"          # nécessite une family file "vikidia" pour pywikibot
FILE_NAMESPACE = 6          # "Fichier" sur Vikidia FR
ARTICLE_NAMESPACES = [0]    # namespace des articles (0 = principal)

POLL_INTERVAL = 600          # secondes entre chaque vérification des RC
DRY_RUN = False              # True = simulation, ne sauvegarde rien
EDIT_SUMMARY = "Bot : suppression du lien vers une image supprimée"
SLEEP_BETWEEN_EDITS = 2     # secondes, pour ne pas spammer l'API
STATE_FILE = "last_check.txt"  # persiste le dernier timestamp vérifié entre les redémarrages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("vikidia-image-cleanup")


# ---------------------------------------------------------------------------
# REGEX pour capturer [[Fichier:xxx|...]] ou [[File:xxx|...]]
# y compris les usages en galerie <gallery>Fichier:xxx</gallery> (basique)
# ---------------------------------------------------------------------------

def build_image_pattern(file_title_no_ns: str) -> re.Pattern:
    """
    Construit un pattern qui matche [[Fichier:nom.ext...]] ou [[File:nom.ext...]]
    avec tous les paramètres (thumb, taille, légende, etc.), sur une ou
    plusieurs lignes (légendes multi-lignes possibles).
    """
    escaped = re.escape(file_title_no_ns)
    pattern = (
        r"\[\[\s*(?:[Ff]ichier|[Ff]ile|[Ii]mage)\s*:\s*"
        + escaped
        + r"\s*(\|[^\[\]]*(?:\[\[[^\[\]]*\][^\[\]]*\])*[^\[\]]*)?\]\]"
    )
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


def clean_text(text: str, file_title_no_ns: str) -> str:
    """Retire les occurrences de l'image du wikicode."""
    pattern = build_image_pattern(file_title_no_ns)
    new_text = pattern.sub("", text)

    # Nettoyage des balises <gallery>Fichier:xxx.png|légende</gallery>
    gallery_line_pattern = re.compile(
        r"^[ \t]*(?:[Ff]ichier|[Ff]ile|[Ii]mage)\s*:\s*"
        + re.escape(file_title_no_ns)
        + r"[^\n]*\n?",
        re.IGNORECASE | re.MULTILINE,
    )
    new_text = gallery_line_pattern.sub("", new_text)

    return new_text


# ---------------------------------------------------------------------------
# DÉTECTION DES SUPPRESSIONS D'IMAGES
# ---------------------------------------------------------------------------

def get_recent_file_deletions(site: pywikibot.site.APISite, since: datetime):
    """
    Récupère les entrées du journal de suppression concernant le
    namespace Fichier depuis `since`.
    Retourne une liste de titres de fichiers (avec préfixe namespace).
    """
    deleted_files = []
    for entry in site.logevents(
        logtype="delete",
        namespace=FILE_NAMESPACE,
        start=since,
        end=None,
        reverse=True,  # du plus ancien au plus récent
    ):
        try:
            title = entry.page().title()
        except Exception:
            continue
        # Seules les suppressions de page (pas restaurations/révisions) nous intéressent
        if entry.action() in ("delete", "delete_redir"):
            deleted_files.append(title)
    return deleted_files


# ---------------------------------------------------------------------------
# TRAITEMENT D'UNE IMAGE SUPPRIMÉE
# ---------------------------------------------------------------------------

def process_deleted_file(site: pywikibot.site.APISite, file_title: str):
    """
    Pour un fichier supprimé donné (ex: 'Fichier:Exemple.png'),
    trouve les pages qui le référencent et nettoie le wikicode.
    """
    log.info("Traitement de l'image supprimée : %s", file_title)

    file_page = pywikibot.FilePage(site, file_title)

    # Vérification explicite : on ne traite que si le fichier n'existe
    # VRAIMENT plus (pas juste une entrée de log ambiguë, une suppression
    # de révision spécifique, ou une image ré-uploadée entre-temps).
    try:
        if file_page.exists():
            log.info("%s existe toujours (probablement ré-uploadée), on ignore.", file_title)
            return
    except Exception as e:
        log.warning("Impossible de vérifier l'existence de %s (%s), on continue par prudence.", file_title, e)

    file_title_no_ns = file_page.title(with_ns=False)

    # usingPages() interroge la table imagelinks (usage réel de l'image via
    # [[Fichier:...]]), à ne pas confondre avec backlinks() qui interroge
    # pagelinks (liens explicites du type [[:Fichier:...]]).
    # Ça fonctionne même après suppression du fichier, car les entrées
    # imagelinks des AUTRES pages ne sont effacées qu'au prochain
    # ré-examen (édition/job) de ces pages-là, pas de la page supprimée.
    try:
        using_pages = list(
            site.imageusage(file_page, namespaces=ARTICLE_NAMESPACES, total=None, content=False)
        )
    except Exception as e:
        log.error("Impossible de récupérer les pages utilisant %s : %s", file_title, e)
        return

    if not using_pages:
        log.info("Aucune page ne référence %s, rien à faire.", file_title)
        return

    log.info("%d page(s) référencent %s", len(using_pages), file_title)

    for page in using_pages:
        try:
            old_text = page.text
        except Exception as e:
            log.warning("Impossible de lire %s : %s", page.title(), e)
            continue

        new_text = clean_text(old_text, file_title_no_ns)

        if new_text == old_text:
            log.debug("Aucune occurrence textuelle trouvée dans %s (syntaxe non reconnue ?)", page.title())
            continue

        log.info("→ Nettoyage de la page : %s", page.title())

        if DRY_RUN:
            log.info("[DRY_RUN] Sauvegarde simulée pour %s", page.title())
        else:
            try:
                page.text = new_text
                page.save(summary=EDIT_SUMMARY, minor=False, botflag=True)
                log.info("✓ Page sauvegardée : %s", page.title())
            except Exception as e:
                log.error("✗ Échec de la sauvegarde de %s : %s", page.title(), e)

        time.sleep(SLEEP_BETWEEN_EDITS)


# ---------------------------------------------------------------------------
# BOUCLE PRINCIPALE
# ---------------------------------------------------------------------------

def load_last_check() -> datetime:
    """Charge le dernier timestamp vérifié depuis le disque, ou 'maintenant' si absent (premier lancement)."""
    try:
        with open(STATE_FILE, "r") as f:
            ts = f.read().strip()
            return datetime.fromisoformat(ts)
    except (FileNotFoundError, ValueError):
        return datetime.now(timezone.utc)


def save_last_check(ts: datetime):
    with open(STATE_FILE, "w") as f:
        f.write(ts.isoformat())


def main():
    site = pywikibot.Site(LANG, FAMILY)
    site.login()

    log.info("Bot démarré sur %s (DRY_RUN=%s)", site, DRY_RUN)

    already_processed = set()
    last_check = load_last_check()
    log.info("Surveillance des suppressions à partir de : %s", last_check.isoformat())

    while True:
        try:
            now = datetime.now(timezone.utc)
            deleted_files = get_recent_file_deletions(site, since=last_check)

            for file_title in deleted_files:
                if file_title in already_processed:
                    continue
                process_deleted_file(site, file_title)
                already_processed.add(file_title)

            # Garder le set raisonnable en mémoire
            if len(already_processed) > 5000:
                already_processed.clear()

            last_check = now - timedelta(minutes=1)  # petite marge de sécurité
            save_last_check(last_check)
        except KeyboardInterrupt:
            log.info("Arrêt demandé par l'utilisateur.")
            sys.exit(0)
        except Exception as e:
            log.error("Erreur dans la boucle principale : %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
