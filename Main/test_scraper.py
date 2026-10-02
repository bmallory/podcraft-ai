#!/usr/bin/env python3
"""
Script de test et de diagnostic pour le scraping de la Lambda Main.
Permet d'évaluer rapidement la récupération des articles sans exécuter
le reste du pipeline (Gemini, S3, FFmpeg).

Usage:
  python3 test_scraper.py                  # Teste toutes les sources configurées
  python3 test_scraper.py "Numerama"       # Teste une source spécifique
  python3 test_scraper.py --direct         # Force le mode direct (sans proxy)
  python3 test_scraper.py --use-proxy      # Force l'utilisation du proxy
  python3 test_scraper.py --max 3          # Nombre d'articles à tester par source
"""

import sys
import json
import argparse
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests

import app

BLOCKED_PATTERNS = [
    "please enable js",
    "javascript is disabled",
    "client challenge",
    "captcha",
    "attention required! | cloudflare",
    "nous mettons tout en œuvre pour rétablir le service",
    "access denied",
    "robot or human",
    "datadome"
]


def check_for_antibot(text: str, status_code: int) -> str:
    """Détecte si la réponse correspond à une page de challenge / blocage bot."""
    lower_text = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lower_text:
            return f"BLOCKED (Détecté: '{pattern}')"
    if status_code in (403, 429, 503):
        return f"HTTP {status_code} (Blocage ou limitation probable)"
    if len(text.strip()) == 0:
        return "EMPTY (0 caractère extrait)"
    return "OK"


def run_diagnostic(source_filter=None, force_mode=None, max_articles_to_test=2):
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    proxy_config = config.get('proxy', {})
    sources = config.get('sources', [])

    if source_filter:
        sources = [s for s in sources if source_filter.lower() in s['name'].lower()]
        if not sources:
            print(f"❌ Aucune source correspondant à '{source_filter}'")
            return

    proxy_endpoint_display = proxy_config.get('endpoint') or 'Non configuré'
    has_api_key = bool(proxy_config.get('api_key') and proxy_config.get('api_key') != '[TOSET]')

    print("=" * 80)
    print("🔍 DIAGNOSTIC DE SCRAPING - LAMBDA MAIN (THÉMATIQUE)")
    print(f"Proxy global: endpoint={proxy_endpoint_display} | api_key={'set' if has_api_key else 'none'}")
    print("=" * 80)

    summary = []

    for source in sources:
        name = source['name']
        is_rss = source.get('isRss', False)
        if isinstance(is_rss, str):
            is_rss = is_rss.lower() == 'true'

        src_proxy = source.get('use_proxy', False)
        if force_mode == 'direct':
            src_proxy = False
            curr_proxy_cfg = {'enabled': False}
        elif force_mode == 'proxy':
            src_proxy = True
            curr_proxy_cfg = proxy_config
        else:
            curr_proxy_cfg = proxy_config

        src_copy = dict(source)
        src_copy['use_proxy'] = src_proxy

        print(f"\n📡 Source: [{name}]")
        print(f"   URL: {source['url']}")
        print(f"   Type: {'RSS' if is_rss else 'Page d\'accueil (HTML)'} | Proxy: {src_proxy}")

        # 1. Récupération des articles
        try:
            articles = app.get_articles_from_source(src_copy, curr_proxy_cfg)
        except Exception as e:
            print(f"   ❌ Erreur récupération: {e}")
            articles = []

        print(f"   🔗 Articles candidats trouvés: {len(articles)}")
        if not articles:
            summary.append({'name': name, 'links': 0, 'status': '❌ AUCUN ARTICLE', 'raw_chars': 0, 'effective_chars': 0})
            continue

        # 2. Test d'extraction d'articles
        articles_tested = articles[:max_articles_to_test]
        source_raw_chars = 0
        source_effective_chars = 0
        all_status = []

        for idx, item in enumerate(articles_tested, 1):
            link = item['url']
            rss_title = item.get('title', '').strip()
            rss_summary = item.get('summary', '').strip()

            print(f"\n   📄 Article #{idx}: {link}")
            try:
                resp = app.fetch_url(link, proxy_config=curr_proxy_cfg, source_config=src_copy)
                status_code = resp.status_code
                content_len = len(resp.content)
            except requests.HTTPError as he:
                status_code = he.response.status_code if he.response else 'Error'
                content_len = 0
            except Exception as e:
                status_code = f"Error: {e}"
                content_len = 0

            # Extraction HTML
            raw_text = app.scrape_article_text(link, proxy_config=curr_proxy_cfg, source_config=src_copy)
            raw_len = len(raw_text)
            source_raw_chars += raw_len

            antibot_check = check_for_antibot(raw_text, status_code if isinstance(status_code, int) else 200)
            all_status.append(antibot_check)

            # Logique de fallback
            if raw_text and len(raw_text) >= 150:
                final_text = raw_text[:2000]
                source_used = "HTML (Scraping)"
            elif rss_summary or rss_title:
                final_text = f"{rss_title}. {rss_summary}" if rss_title and rss_summary else (rss_summary or rss_title)
                source_used = "RSS Fallback"
            else:
                final_text = ""
                source_used = "Aucun"

            source_effective_chars += len(final_text)

            print(f"      • Statut HTTP: {status_code} | Taille brute: {content_len} octets")
            print(f"      • Scraping brut: {raw_len} car. | Diagnostic: {antibot_check}")
            print(f"      • Contenu retenu: {len(final_text)} car. via [{source_used}]")
            if final_text:
                preview = final_text[:120].replace('\n', ' ')
                print(f"      • Aperçu: \"{preview}...\"")

        # Statut global pour la source
        if source_effective_chars > 0:
            if source_raw_chars > 0:
                final_status = "✅ OK (HTML direct)"
            else:
                final_status = "✅ OK (Sauvé par RSS Fallback)"
        elif any('BLOCKED' in s or 'HTTP 403' in s for s in all_status):
            final_status = "❌ BLOQUÉ (Anti-bot)"
        else:
            final_status = "⚠️ VIDE"

        summary.append({
            'name': name,
            'links': len(articles),
            'status': final_status,
            'raw_chars': source_raw_chars,
            'effective_chars': source_effective_chars
        })

    # Affichage du bilan final
    print("\n" + "=" * 80)
    print("📊 BILAN RÉCAPITULATIF DU SCRAPING")
    print("=" * 80)
    print(f"{'Source':<32} | {'Articles':<8} | {'Scraping Brut':<14} | {'Retenu (Final)':<15} | {'Résultat'}")
    print("-" * 95)
    for s in summary:
        print(f"{s['name']:<32} | {s['links']:<8} | {s.get('raw_chars', 0):<14} | {s.get('effective_chars', 0):<15} | {s['status']}")
    print("=" * 95)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Outil de diagnostic du scraping pour la Lambda Main")
    parser.add_argument("source", nargs="?", default=None, help="Nom de la source à tester (optionnel)")
    parser.add_argument("--direct", action="store_true", help="Désactiver le proxy pour le test")
    parser.add_argument("--use-proxy", action="store_true", help="Forcer l'utilisation du proxy pour le test")
    parser.add_argument("--max", type=int, default=2, help="Nombre max d'articles à tester par source (défaut: 2)")
    args = parser.parse_args()

    mode = None
    if args.direct:
        mode = 'direct'
    elif args.use_proxy:
        mode = 'proxy'

    run_diagnostic(source_filter=args.source, force_mode=mode, max_articles_to_test=args.max)
