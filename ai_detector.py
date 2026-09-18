#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de détection d'images IA via l'API Winston AI.
Si le score de probabilité IA dépasse 95 %, ajoute {{image IA}} en haut du fichier Vikidia.

Mode test ponctuel :
    python ai_detector.py "Fichier:Exemple.png" --dry-oui
    python ai_detector.py --file "Fichier:Exemple.png"

Mode surveillance continue (lancé via main.py) :
    python ai_detector.py
"""

import argparse
import logging
import os
import sys
import time
import requests
import pywikibot

# ---------------------------------------------------------------------------
# CHARGEMENT AUTOMATIQUE DU FICHIER .env
# ---------------------------------------------------------------------------

def load_env_file(filepath=".env"):
    """Lit le fichier .env et injecte les variables dans os.environ."""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip("'\"")

load_env_file()

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

WINSTON_API_KEY = os.getenv("WINSTON_API_KEY", "").strip()
WINSTON_API_URL = "https://api.gowinston.ai/v2/image-detection"

CONFIDENCE_THRESHOLD = 95.0  # Seuil en %
TAG_TEMPLATE = "{{image IA}}\n"
EDIT_SUMMARY = "Bot : ajout de {{image IA}} (détection automatique Winston AI > 95 %)"
POLL_INTERVAL = 600  # Intervalle de sondage en secondes (10 min)

# ---------------------------------------------------------------------------
# WEBHOOK DISCORD (logs dédiés à ce script uniquement)
# ---------------------------------------------------------------------------

AI_DETECTOR_WEBHOOK_URL = os.getenv("AI_DETECTOR_WEBHOOK_URL", "").strip()
EMBED_COLOR_AI_DETECTED = 0xE74C3C   # rouge
EMBED_COLOR_CLEAN = 0x2ECC71         # vert
EMBED_COLOR_SKIP = 0x95A5A6          # gris
EMBED_COLOR_ERROR = 0xF1C40F         # jaune
EMBED_COLOR_DRYRUN = 0x3498DB        # bleu

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("winston-ai-detector")


def send_discord_embed(title: str, description: str, color: int, fields=None, thumbnail_url: str = None):
    """
    Envoie un embed Discord via webhook. N'échoue jamais bruyamment :
    si le webhook n'est pas configuré ou si l'envoi échoue, on logue et on continue.
    """
    if not AI_DETECTOR_WEBHOOK_URL:
        return

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": pywikibot.Timestamp.now().isoformat(),
        "footer": {"text": "ai_detector · Winston AI"},
    }
    if fields:
        embed["fields"] = [
            {"name": name, "value": str(value), "inline": inline}
            for name, value, inline in fields
        ]
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(AI_DETECTOR_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code >= 300:
            log.warning("Webhook Discord : réponse inattendue (%s) : %s", resp.status_code, resp.text[:200])
    except requests.exceptions.RequestException as e:
        log.warning("Webhook Discord : envoi impossible : %s", e)


def check_image_with_winston(image_url: str) -> float:
    """
    Interroge l'API Winston AI et retourne le score d'IA en pourcentage (0.0 à 100.0).
    L'API retourne un 'score' qui est le score d'humanité (0 = IA, 100 = Humain).
    """
    if not WINSTON_API_KEY:
        log.error("Aucune clé API Winston AI détectée ! Vérifie ton fichier .env ou ta variable WINSTON_API_KEY.")
        return 0.0

    headers = {
        "Authorization": f"Bearer {WINSTON_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"url": image_url}

    try:
        response = requests.post(WINSTON_API_URL, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()

        # Score d'humanité renvoyé par Winston AI (0 = 100% IA, 100 = 100% Humain)
        human_score = float(data.get("score", 100.0))
        ai_score = 100.0 - human_score
        return ai_score

    except requests.exceptions.HTTPError as e:
        if response.status_code == 401:
            log.error("Erreur 401 (Non autorisé) : clé API Winston AI invalide ou expirée.")
        else:
            log.error("Erreur HTTP API Winston AI (%s) : %s", response.status_code, e)
        return 0.0
    except requests.exceptions.RequestException as e:
        log.error("Erreur lors de la requête API Winston AI : %s", e)
        return 0.0


def process_file_page(site: pywikibot.Site, page_title: str, dry_run: bool = False):
    """
    Analyse la page de fichier spécifiée et pose le bandeau {{image IA}} si le score IA >= 95 %.
    """
    try:
        file_page = pywikibot.FilePage(site, page_title)
        if not file_page.exists():
            log.error("La page %s n'existe pas.", page_title)
            return

        text = file_page.text

        # Évite de retraiter si le modèle est déjà présent
        if "{{image IA}}" in text or "{{Image IA}}" in text:
            log.info("%s contient déjà {{image IA}}, passage.", page_title)
            send_discord_embed(
                title="⏭️ Déjà taggé",
                description=f"**{page_title}** contient déjà `{{{{image IA}}}}`.",
                color=EMBED_COLOR_SKIP,
                fields=[("Page", page_title, False)],
            )
            return

        image_url = file_page.get_file_url()
        log.info("Analyse de %s (URL : %s)...", page_title, image_url)

        score = check_image_with_winston(image_url)
        log.info("Score IA calculé pour %s : %.2f %%", page_title, score)

        page_url = file_page.full_url()

        if score >= CONFIDENCE_THRESHOLD:
            log.warning("Image détectée IA avec une certitude de %.2f %% !", score)
            new_text = TAG_TEMPLATE + text

            if dry_run:
                log.info("[DRY-RUN] Mode simulation : {{image IA}} aurait été ajouté sur %s", page_title)
                send_discord_embed(
                    title="🧪 [DRY-RUN] Image IA détectée",
                    description=f"[**{page_title}**]({page_url})\nAurait reçu le bandeau `{{{{image IA}}}}`.",
                    color=EMBED_COLOR_DRYRUN,
                    fields=[
                        ("Score IA", f"{score:.2f} %", True),
                        ("Seuil", f"{CONFIDENCE_THRESHOLD:.2f} %", True),
                    ],
                    thumbnail_url=image_url,
                )
            else:
                file_page.text = new_text
                file_page.save(summary=EDIT_SUMMARY, minor=False, bot=True)
                log.info("✓ Bandeau {{image IA}} ajouté sur %s.", page_title)
                send_discord_embed(
                    title="🚨 Image IA détectée et taggée",
                    description=f"[**{page_title}**]({page_url})\nBandeau `{{{{image IA}}}}` ajouté.",
                    color=EMBED_COLOR_AI_DETECTED,
                    fields=[
                        ("Score IA", f"{score:.2f} %", True),
                        ("Seuil", f"{CONFIDENCE_THRESHOLD:.2f} %", True),
                    ],
                    thumbnail_url=image_url,
                )
        else:
            log.info("Score (%.2f %%) sous le seuil de %.2f %%, aucune action.", score, CONFIDENCE_THRESHOLD)
            send_discord_embed(
                title="✅ Image jugée saine",
                description=f"[**{page_title}**]({page_url})",
                color=EMBED_COLOR_CLEAN,
                fields=[
                    ("Score IA", f"{score:.2f} %", True),
                    ("Seuil", f"{CONFIDENCE_THRESHOLD:.2f} %", True),
                ],
                thumbnail_url=image_url,
            )

    except Exception as e:
        log.error("Erreur lors du traitement de %s : %s", page_title, e)
        send_discord_embed(
            title="⚠️ Erreur lors de l'analyse",
            description=f"**{page_title}**\n```{str(e)[:500]}```",
            color=EMBED_COLOR_ERROR,
        )


def watch_uploads_for_ai(site: pywikibot.Site, dry_run: bool = False):
    """
    Sonde périodiquement le journal des téléversements pour analyser automatiquement
    chaque nouvelle image ajoutée sur le wiki.
    """
    last_timestamp = pywikibot.Timestamp.now()
    last_logid = None
    log.info("Surveillance continue des téléversements démarrée pour la détection d'images IA.")

    while True:
        try:
            events = list(site.logevents(logtype="upload", start=last_timestamp, reverse=True))
            for event in events:
                try:
                    logid = event.logid()
                except Exception:
                    logid = None

                ts = event.timestamp()
                if last_logid is not None and logid == last_logid:
                    continue

                try:
                    page = event.page()
                    process_file_page(site, page.title(), dry_run=dry_run)
                except Exception as e:
                    log.error("Erreur sur l'événement de téléversement : %s", e)

                last_timestamp = ts
                last_logid = logid

        except Exception as e:
            log.error("Erreur dans la boucle de surveillance IA : %s", e)

        time.sleep(POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="Détection d'images IA via Winston AI pour Vikidia.")
    parser.add_argument("positional_file", nargs="?", default=None, help="Nom du fichier à tester")
    parser.add_argument("--file", "-f", "--image", dest="option_file", default=None, help="Nom du fichier à tester")
    parser.add_argument("--dry-oui", "--dry-run", action="store_true", dest="dry_run", help="Active le mode simulation")

    args = parser.parse_args()
    target_file = args.option_file or args.positional_file

    site = pywikibot.Site("fr", "vikidia")

    if WINSTON_API_KEY:
        masked_key = WINSTON_API_KEY[:4] + "..." + WINSTON_API_KEY[-4:] if len(WINSTON_API_KEY) > 8 else "***"
        log.info("Clé API Winston AI chargée : %s", masked_key)
    else:
        log.error("ATTENTION : Aucune clé WINSTON_API_KEY trouvée dans le fichier .env !")
        send_discord_embed(
            title="⚠️ Clé Winston AI manquante",
            description="`WINSTON_API_KEY` est absente ou vide dans `.env` — le bot ne pourra pas analyser d'images.",
            color=EMBED_COLOR_ERROR,
        )

    if target_file:
        log.info("=== Mode test ponctuel sur : %s ===", target_file)
        log.info("Mode : %s", "SIMULATION (--dry-oui)" if args.dry_run else "RÉEL / PRODUCTION")
        process_file_page(site, target_file, dry_run=args.dry_run)
    else:
        log.info("=== Mode surveillance continue des téléversements ===")
        watch_uploads_for_ai(site, dry_run=args.dry_run)


if __name__ == "__main__":
    main()