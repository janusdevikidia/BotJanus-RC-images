#!/usr/bin/env python3
"""
BotJanus — surveillance continue des nouveaux téléversements.[span_0](start_span)[span_0](end_span)

Vikidia n'étant pas un wiki Wikimedia, il n'y a pas d'accès à EventStreams
(stream.wikimedia.org, réservé aux projets WMF). On sonde donc périodiquement le
journal des téléversements (action=query&list=logevents&letype=upload) via l'API,
et on lance l'analyse de licence sur chaque nouveau fichier détecté.[span_1](start_span)[span_1](end_span)

Logique d'analyse (par fichier) :
  1. Templates réellement transclus sur la page (résolus par MediaWiki, alias compris)
     comparés à la liste des modèles listés récursivement dans Catégorie:Modèle licence.[span_2](start_span)[span_2](end_span)
  2. Licence reconnue trouvée => rien à faire.[span_3](start_span)[span_3](end_span)
  3. Aucune licence reconnue => on ajoute {{LI}} en haut de la page, et 
     on ajoute une section "== Licence manquante ==" avec {{subst:Image oubli|nom_du_fichier}} sur la page de
     discussion du téléverseur.[span_4](start_span)[span_4](end_span)

Toutes les actions sont idempotentes (vérification avant écriture), donc un même
événement retraité par erreur (redémarrage du bot, chevauchement de fenêtre de sondage)
ne produit pas de doublon.[span_5](start_span)[span_5](end_span)
"""

import logging
import sys
import time
import pywikibot

# ==========================================================================
# CONFIGURATION
# ==========================================================================

DRY_RUN = False               # True = simulation, aucune écriture sur le wiki[span_6](start_span)[span_6](end_span)
POLL_INTERVAL = 600            # secondes entre deux sondages du journal des téléversements[span_7](start_span)[span_7](end_span)
PROCESS_BACKLOG = False       # False = ne traite que les téléversements à partir du démarrage[span_8](start_span)[span_8](end_span)
                               # True = traite aussi les téléversements déjà existants (attention,
                               # peut représenter énormément de pages au premier lancement)[span_9](start_span)[span_9](end_span)

LICENSE_MODELS_CATEGORY = "Modèle licence"               # Catégorie:Modèle licence[span_10](start_span)[span_10](end_span)
UNKNOWN_LICENSE_TEMPLATES = {"Licence inconnue", "LI"}    # marqueurs "pas de licence", exclus[span_11](start_span)[span_11](end_span)

LI_TEMPLATE_TEXT = "{{LI}}\n[span_12](start_span)"[span_12](end_span)

TALK_SECTION_TITLE = "Licence manquante[span_13](start_span)"[span_13](end_span)
TALK_MESSAGE = "{{subst:Image oubli|%s}} ~~~~[span_14](start_span)"[span_14](end_span)

EDIT_SUMMARY_FILE = "Bot : pose de {{LI}} — aucune licence reconnue détectée sur ce fichier[span_15](start_span)"[span_15](end_span)
EDIT_SUMMARY_TALK = "Bot : notification — licence manquante sur un fichier téléversé[span_16](start_span)"[span_16](end_span)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)[span_17](start_span)[span_17](end_span)
log = logging.getLogger("botjanus")[span_18](start_span)[span_18](end_span)


# ==========================================================================
# DÉTECTION DE LICENCE
# ==========================================================================

def get_known_license_templates(site):
    """Ensemble des titres (sans espace de noms) de tous les modèles listés,
    récursivement, dans Catégorie:Modèle licence — hors marqueurs 'licence inconnue'.""[span_19](start_span)"[span_19](end_span)
    category = pywikibot.Category(site, LICENSE_MODELS_CATEGORY)[span_20](start_span)[span_20](end_span)
    members = category.members(namespaces=[10], recurse=True)  # 10 = Modèle/Template[span_21](start_span)[span_21](end_span)
    known = {page.title(with_ns=False) for page in members}[span_22](start_span)[span_22](end_span)

    return known - UNKNOWN_LICENSE_TEMPLATES[span_23](start_span)[span_23](end_span)


def page_has_recognized_license(page, known_license_templates):
    try:
        templates = list(page.itertemplates())[span_24](start_span)[span_24](end_span)
    except Exception as e:
        log.warning("  Erreur lors de la récupération des templates de %s : %s", page.title(), e)[span_25](start_span)[span_25](end_span)
        return False, set()[span_26](start_span)[span_26](end_span)

    template_titles = {t.title(with_ns=False) for t in templates}[span_27](start_span)[span_27](end_span)
    found = template_titles & known_license_templates[span_28](start_span)[span_28](end_span)
    return bool(found), template_titles[span_29](start_span)[span_29](end_span)


def page_already_tagged_li(template_titles):
    return bool(template_titles & UNKNOWN_LICENSE_TEMPLATES)[span_30](start_span)[span_30](end_span)


def add_li_banner(page, dry_run):
    text = page.text[span_31](start_span)[span_31](end_span)
    new_text = LI_TEMPLATE_TEXT + text[span_32](start_span)[span_32](end_span)

    if dry_run:
        log.info("  -> [DRY-RUN] aurait ajouté {{LI}} en haut de %s", page.title())[span_33](start_span)[span_33](end_span)
        return

    page.text = new_text[span_34](start_span)[span_34](end_span)
    page.save(summary=EDIT_SUMMARY_FILE, minor=False, bot=False)[span_35](start_span)[span_35](end_span)
    log.info("  -> {{LI}} ajouté avec succès sur %s", page.title())[span_36](start_span)[span_36](end_span)


def notify_uploader(site, page, dry_run):
    try:
        uploader_name = page.oldest_file_info.user[span_37](start_span)[span_37](end_span)
    except Exception as e:
        log.warning("  Impossible de déterminer le téléverseur de %s : %s", page.title(), e)[span_38](start_span)[span_38](end_span)
        return

    user = pywikibot.User(site, uploader_name)[span_39](start_span)[span_39](end_span)
    talk_page = user.getUserTalkPage()[span_40](start_span)[span_40](end_span)

    try:
        talk_text = talk_page.text if talk_page.exists() else "[span_41](start_span)"[span_41](end_span)
    except Exception as e:
        log.warning("  Erreur de lecture de la page de discussion de %s : %s", uploader_name, e)[span_42](start_span)[span_42](end_span)
        return

    # Récupération du titre du fichier
    file_title_without_ns = page.title(with_ns=False)[span_43](start_span)[span_43](end_span)
    
    # Vérification : si le fichier est déjà cité dans la PDD, on n'ajoute rien
    if file_title_without_ns in talk_text:[span_44](start_span)[span_44](end_span)
        log.info("  -> notification pour %s déjà présente chez %s, on ignore.", file_title_without_ns, uploader_name)[span_45](start_span)[span_45](end_span)
        return[span_46](start_span)[span_46](end_span)

    section_header = "== %s ==" % TALK_SECTION_TITLE[span_47](start_span)[span_47](end_span)
    formatted_talk_message = TALK_MESSAGE % file_title_without_ns[span_48](start_span)[span_48](end_span)

    # Construction du bloc à ajouter en fin de page
    new_block = "%s\n%s" % (section_header, formatted_talk_message)[span_49](start_span)[span_49](end_span)

    separator = "\n\n" if talk_text.strip() else "[span_50](start_span)"[span_50](end_span)
    new_talk_text = "%s%s%s\n" % (talk_text.strip(), separator, new_block)[span_51](start_span)[span_51](end_span)

    if dry_run:
        log.info("  -> [DRY-RUN] aurait ajouté l'avertissement pour %s chez %s", file_title_without_ns, uploader_name)[span_52](start_span)[span_52](end_span)
        return

    talk_page.text = new_talk_text[span_53](start_span)[span_53](end_span)
    talk_page.save(summary=EDIT_SUMMARY_TALK, minor=False, bot=False)[span_54](start_span)[span_54](end_span)
    log.info("  -> avertissement pour %s ajouté chez %s", file_title_without_ns, uploader_name)[span_55](start_span)[span_55](end_span)


def process_file(site, page, known_license_templates, dry_run):
    log.info("Analyse de %s", page.title())[span_56](start_span)[span_56](end_span)

    if not page.exists():
        log.info("  -> la page n'existe pas (déjà supprimée ?), on ignore.")[span_57](start_span)[span_57](end_span)
        return

    has_license, template_titles = page_has_recognized_license(page, known_license_templates)[span_58](start_span)[span_58](end_span)

    if has_license:
        log.info("  -> licence détectée (%s), rien à faire.", template_titles & known_license_templates)[span_59](start_span)[span_59](end_span)
        return

    if page_already_tagged_li(template_titles):
        log.info("  -> déjà taggé {{LI}}, on ne repose pas le bandeau.")[span_60](start_span)[span_60](end_span)
    else:
        add_li_banner(page, dry_run)[span_61](start_span)[span_61](end_span)

    notify_uploader(site, page, dry_run)[span_62](start_span)[span_62](end_span)


# ==========================================================================
# SURVEILLANCE DU JOURNAL DES TÉLÉVERSEMENTS
# ==========================================================================

def fetch_new_uploads(site, since_timestamp):
    """Renvoie la liste des événements de téléversement depuis `since_timestamp` (inclus),
    du plus ancien au plus récent.""[span_63](start_span)"[span_63](end_span)
    return list(site.logevents(logtype="upload", start=since_timestamp, reverse=True))[span_64](start_span)[span_64](end_span)


def watch_uploads(site, known_license_templates, dry_run):
    if PROCESS_BACKLOG:[span_65](start_span)[span_65](end_span)
        # None = pas de borne de début -> tout l'historique des téléversements existants
        last_timestamp = None[span_66](start_span)[span_66](end_span)
        last_logid = None[span_67](start_span)[span_67](end_span)
        log.info("PROCESS_BACKLOG=True : traitement de tout l'historique des téléversements.")[span_68](start_span)[span_68](end_span)
    else:
        last_timestamp = pywikibot.Timestamp.now()[span_69](start_span)[span_69](end_span)
        last_logid = None[span_70](start_span)[span_70](end_span)
        log.info("Surveillance à partir de maintenant (%s UTC), backlog ignoré.", last_timestamp.isoformat())[span_71](start_span)[span_71](end_span)

    log.info("Bot démarré, sondage toutes les %d secondes.", POLL_INTERVAL)[span_72](start_span)[span_72](end_span)

    while True:
        try:
            events = fetch_new_uploads(site, last_timestamp)[span_73](start_span)[span_73](end_span)
        except Exception as e:
            log.error("Erreur lors du sondage du journal des téléversements : %s", e)[span_74](start_span)[span_74](end_span)
            time.sleep(POLL_INTERVAL)[span_75](start_span)[span_75](end_span)
            continue

        for event in events:
            try:
                logid = event.logid()[span_76](start_span)[span_76](end_span)
            except Exception:
                logid = None[span_77](start_span)[span_77](end_span)

            ts = event.timestamp()[span_78](start_span)[span_78](end_span)

            # Évite de retraiter le tout dernier événement déjà vu au sondage précédent
            # (la fenêtre 'start' est inclusive).
            if last_logid is not None and logid == last_logid:[span_79](start_span)[span_79](end_span)
                continue

            try:
                page = event.page()[span_80](start_span)[span_80](end_span)
            except Exception as e:
                log.warning("Impossible de récupérer la page associée à l'événement : %s", e)[span_81](start_span)[span_81](end_span)
                continue

            try:
                process_file(site, page, known_license_templates, dry_run=dry_run)[span_82](start_span)[span_82](end_span)
            except Exception as e:
                log.error("Erreur inattendue lors du traitement de %s : %s", page.title(), e)[span_83](start_span)[span_83](end_span)

            last_timestamp = ts[span_84](start_span)[span_84](end_span)
            last_logid = logid[span_85](start_span)[span_85](end_span)

        time.sleep(POLL_INTERVAL)[span_86](start_span)[span_86](end_span)


def main():
    if DRY_RUN:[span_87](start_span)[span_87](end_span)
        log.info("=== MODE DRY-RUN : aucune modification ne sera écrite sur le wiki ===")[span_88](start_span)[span_88](end_span)

    site = pywikibot.Site()[span_89](start_span)[span_89](end_span)
    site.login()[span_90](start_span)[span_90](end_span)

    log.info("Récupération des modèles de licence reconnus (Catégorie:Modèle licence)...")[span_91](start_span)[span_91](end_span)
    known_license_templates = get_known_license_templates(site)[span_92](start_span)[span_92](end_span)
    log.info("%d modèle(s) de licence reconnu(s).", len(known_license_templates))[span_93](start_span)[span_93](end_span)

    try:
        watch_uploads(site, known_license_templates, dry_run=DRY_RUN)[span_94](start_span)[span_94](end_span)
    except KeyboardInterrupt:
        log.info("Arrêt demandé (Ctrl+C), fin du bot.")[span_95](start_span)[span_95](end_span)


if __name__ == "__main__":
    main()[span_96](start_span)[span_96](end_span)
