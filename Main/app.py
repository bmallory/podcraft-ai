import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin, urlparse

import boto3
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class PodcastResponse(BaseModel):
    script: str = Field(
        description="Le script audio complet prononcé par l'animateur/animatrice (uniquement ses paroles orales, sans aucune balise ni mention technique, durée 8 à 12 minutes)."
    )
    description: str = Field(
        description="La description synthétique de l'épisode pour le flux RSS et les plateformes de podcast, obligatoirement encapsulée dans un bloc CDATA avec des balises HTML légères (<p> et <a href=\"...\">)."
    )


def format_rss_description(raw_description: str, fallback_title: str = "Édition du jour") -> str:
    """
    Formate la description de l'épisode pour le flux RSS en garantissant
    l'utilisation de balises HTML légères (<p>, <a>) encapsulées dans un bloc CDATA.
    """
    if not raw_description:
        return f"<![CDATA[<p>{fallback_title} présentée par votre animateur.</p>]]>"

    text = raw_description.strip()

    # Retirer d'éventuels blocs de code markdown (ex: ```html ... ``` ou ```xml ... ```)
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    # Extraire le contenu si déjà dans un bloc CDATA
    cdata_match = re.search(r'<!\[CDATA\[(.*?)\]\]>', text, flags=re.DOTALL)
    if cdata_match:
        content = cdata_match.group(1).strip()
    else:
        content = text.replace("<![CDATA[", "").replace("]]>", "").strip()

    # Convertir les liens Markdown [texte](url) résiduels en balises HTML <a href="url">texte</a>
    content = re.sub(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', r'<a href="\2">\1</a>', content)

    # Convertir les URLs brutes non encore balisées en <a href="url">url</a>
    url_pattern = r'(<a\s+[^>]*>.*?</a>)|(https?://[^\s<>"\'\)]+)'
    content = re.sub(
        url_pattern,
        lambda m: m.group(0) if m.group(1) else f'<a href="{m.group(2)}">{m.group(2)}</a>',
        content,
        flags=re.DOTALL | re.IGNORECASE
    )

    # Assurer la présence des balises <p>...</p> si absentes
    if "<p" not in content.lower():
        paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
        if paragraphs:
            content = "".join([f"<p>{p}</p>" for p in paragraphs])
        else:
            content = f"<p>{content}</p>"

    return f"<![CDATA[{content}]]>"


REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

ANTIBOT_SIGNATURES = [
    "please enable js",
    "javascript is disabled",
    "client challenge",
    "attention required! | cloudflare",
    "nous mettons tout en œuvre pour rétablir le service",
    "captcha",
    "access denied",
    "robot or human",
    "datadome",
    "cloudflare ray id",
    "just a moment..."
]

BOILERPLATE_PATTERNS = [
    "pour sauvegarder un article",
    "vous devez être connecté",
    "inscrivez-vous pour personnaliser",
    "abonnez-vous pour lire",
    "ce contenu est réservé aux abonnés",
    "consulter les commentaires",
    "paramétrer les cookies",
    "accepter les cookies",
    "téléchargez l'application",
    "suivez-nous sur google news",
    "en savoir plus sur la gestion de vos données"
]


def fetch_url(url, proxy_config=None, source_config=None):
    use_proxy = False
    if source_config and 'use_proxy' in source_config:
        use_proxy = source_config['use_proxy']

    if use_proxy and proxy_config:
        endpoint = os.environ.get('PROXY_ENDPOINT') or proxy_config.get('endpoint', '')
        api_key = os.environ.get('PROXY_API_KEY') or proxy_config.get('api_key', '')
        if endpoint and endpoint != "[TOSET]":
            params = {'url': url}
            if api_key and api_key != "[TOSET]":
                params['key'] = api_key
            response = requests.get(endpoint, params=params, timeout=15)
        else:
            response = requests.get(url, timeout=10, headers=REQUEST_HEADERS)
    else:
        response = requests.get(url, timeout=10, headers=REQUEST_HEADERS)
    response.raise_for_status()
    return response


def get_links_from_front_page(source_config, proxy_config=None):
    url = source_config['url']
    base_domain = source_config.get('base_domain', '')
    max_links = source_config.get('max_articles', 3)
    container_selector = source_config.get('container_selector')

    try:
        response = fetch_url(url, proxy_config=proxy_config, source_config=source_config)
        soup = BeautifulSoup(response.content, 'html.parser')

        # Limit scope to a specific container if specified
        search_area = soup.select_one(container_selector) if container_selector else soup
        if not search_area:
            print(f"Container {container_selector} not found on {url}, falling back to full page")
            search_area = soup

        links = []
        exclude_keywords = ['login', 'register', 'connexion', 'abonnement', 'inscription', 'compte', 'newsletter', 'privacy', 'legal']

        for a_tag in search_area.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(url, href)

            parsed_url = urlparse(full_url)
            if (not base_domain or base_domain in parsed_url.netloc) and len(parsed_url.path) > 15:
                if any(kw in href.lower() for kw in exclude_keywords):
                    continue

                if full_url not in links:
                    links.append(full_url)
                    if len(links) >= max_links:
                        break

        return links
    except Exception as e:
        print(f"Error fetching front page {url}: {e}")
        return []


def get_articles_from_rss_feed(source_config, proxy_config=None):
    """
    Extrait les articles d'un flux RSS/Atom avec titre, résumé et URL.
    """
    url = source_config['url']
    base_domain = source_config.get('base_domain', '')
    max_links = source_config.get('max_articles', 3)

    try:
        response = fetch_url(url, proxy_config=proxy_config, source_config=source_config)
        content = response.content

        items = []
        exclude_keywords = ['login', 'register', 'connexion', 'abonnement', 'inscription', 'compte', 'newsletter']

        # 1. Parsing via xml.etree.ElementTree
        try:
            root = ET.fromstring(content)
            for elem in root.iter():
                tag = elem.tag.split('}')[-1].lower() if '}' in elem.tag else elem.tag.lower()
                if tag in ('item', 'entry'):
                    link_text = ""
                    title_text = ""
                    desc_text = ""

                    for child in elem:
                        child_tag = child.tag.split('}')[-1].lower() if '}' in child.tag else child.tag.lower()
                        if child_tag == 'title' and not title_text:
                            title_text = (child.text or '').strip()
                        elif child_tag in ('description', 'summary', 'encoded') and not desc_text:
                            raw_desc = (child.text or '').strip()
                            if raw_desc:
                                desc_text = BeautifulSoup(raw_desc, 'html.parser').get_text(separator=' ', strip=True)
                        elif child_tag == 'link' and not link_text:
                            link_text = (child.text or child.get('href') or '').strip()
                        elif child_tag == 'guid' and not link_text:
                            cand = (child.text or '').strip()
                            if cand.startswith('http://') or cand.startswith('https://'):
                                link_text = cand

                    if link_text:
                        full_url = urljoin(url, link_text)
                        parsed = urlparse(full_url)
                        if (not base_domain or base_domain in parsed.netloc) and parsed.scheme in ('http', 'https'):
                            if not any(kw in full_url.lower() for kw in exclude_keywords):
                                if not any(it['url'] == full_url for it in items):
                                    items.append({
                                        'url': full_url,
                                        'title': title_text,
                                        'summary': desc_text
                                    })
                if len(items) >= max_links:
                    break
        except Exception as xml_err:
            print(f"ElementTree parsing failed for RSS feed {url}, falling back to BeautifulSoup: {xml_err}")

        # 2. Fallback BeautifulSoup si ElementTree n'a rien trouvé
        if not items:
            soup = BeautifulSoup(content, 'html.parser')
            for entry in soup.find_all(['item', 'entry']):
                link_tag = entry.find('link')
                link_text = ""
                if link_tag:
                    link_text = link_tag.get('href') or link_tag.get_text(strip=True)
                if not link_text:
                    guid = entry.find('guid')
                    if guid and guid.get_text(strip=True).startswith('http'):
                        link_text = guid.get_text(strip=True)

                title_tag = entry.find('title')
                title_text = title_tag.get_text(strip=True) if title_tag else ""

                desc_tag = entry.find(['description', 'summary'])
                desc_text = desc_tag.get_text(separator=' ', strip=True) if desc_tag else ""

                if link_text:
                    full_url = urljoin(url, link_text)
                    parsed = urlparse(full_url)
                    if (not base_domain or base_domain in parsed.netloc) and parsed.scheme in ('http', 'https'):
                        if not any(kw in full_url.lower() for kw in exclude_keywords):
                            if not any(it['url'] == full_url for it in items):
                                items.append({
                                    'url': full_url,
                                    'title': title_text,
                                    'summary': desc_text
                                })
                if len(items) >= max_links:
                    break

        return items[:max_links]
    except Exception as e:
        print(f"Error fetching/parsing RSS feed {url}: {e}")
        return []


def get_links_from_rss_feed(source_config, proxy_config=None):
    """Garde la compatibilité si appelée directement."""
    items = get_articles_from_rss_feed(source_config, proxy_config)
    return [it['url'] for it in items]


def get_articles_from_source(source_config, proxy_config=None):
    is_rss = source_config.get('isRss', False)
    if isinstance(is_rss, str):
        is_rss = is_rss.lower() == 'true'

    if is_rss:
        return get_articles_from_rss_feed(source_config, proxy_config)
    else:
        links = get_links_from_front_page(source_config, proxy_config)
        return [{'url': l, 'title': '', 'summary': ''} for l in links]


def scrape_article_text(url, proxy_config=None, source_config=None):
    try:
        response = fetch_url(url, proxy_config=proxy_config, source_config=source_config)
        soup = BeautifulSoup(response.content, 'html.parser')

        # 1. Vérification du titre pour détecter les challenges de sécurité
        title_text = soup.title.get_text(strip=True).lower() if soup.title else ""
        if any(sig in title_text for sig in ["client challenge", "attention required", "access denied", "cloudflare ray"]):
            print(f"⚠️ Anti-bot challenge detected in title on {url}: '{title_text}'")
            return ""

        # 2. Extraction et filtrage des paragraphes
        paragraphs = []
        for p in soup.find_all('p'):
            p_text = p.get_text(separator=' ', strip=True)
            if len(p_text) < 40:
                continue
            p_lower = p_text.lower()
            if any(bp in p_lower for bp in BOILERPLATE_PATTERNS):
                continue
            paragraphs.append(p_text)

        text = ' '.join(paragraphs).strip()

        # 3. Vérification des signatures anti-bot dans le corps de texte
        text_lower = text.lower()
        if any(sig in text_lower for sig in ANTIBOT_SIGNATURES):
            print(f"⚠️ Anti-bot signature detected in page content on {url}")
            return ""

        if len(text) < 100:
            return ""

        return text
    except Exception as e:
        print(f"Error scraping article {url}: {e}")
        return ""


def handler(event, context):
    try:
        # Load configuration
        config = {}
        if os.path.exists('config.json'):
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)

        podcast_cfg = config.get('podcast', {})
        proxy_config = config.get('proxy', {})

        # Variabilized metadata with priority: ENV -> config.json -> default
        podcast_title = os.environ.get('PODCAST_TITLE') or podcast_cfg.get('title', "Tech & IA Horizon")
        podcast_host = os.environ.get('PODCAST_HOST') or podcast_cfg.get('host', "Alex, votre guide tech")
        podcast_theme = os.environ.get('PODCAST_THEME') or podcast_cfg.get('theme', "l'Intelligence Artificielle, la robotique et les innovations technologiques")
        podcast_duration = os.environ.get('PODCAST_DURATION') or podcast_cfg.get('target_duration', "8 à 12 minutes")
        podcast_language = os.environ.get('PODCAST_LANGUAGE') or podcast_cfg.get('language', "FRANÇAIS")
        tts_voice = os.environ.get('GEMINI_TTS_VOICE') or podcast_cfg.get('tts_voice', 'Laomedeia')

        script_model = os.environ.get('GEMINI_SCRIPT_MODEL', 'gemini-3.7-flash')
        tts_model = os.environ.get('GEMINI_TTS_MODEL', 'gemini-3.1-flash-tts-preview')

        all_news_text = ""
        scraping_stats = []

        for source in config.get('sources', []):
            source_name = source.get('name', 'Unknown')
            print(f"\n--- Processing source: {source_name} ---")

            articles = get_articles_from_source(source, proxy_config)
            print(f"Found {len(articles)} candidate articles for {source_name}")

            source_text = f"Source : {source_name}\n"
            collected_articles = 0
            collected_chars = 0
            fallback_count = 0

            for item in articles:
                link = item['url']
                rss_title = item.get('title', '').strip()
                rss_summary = item.get('summary', '').strip()

                print(f"Scraping article: {link}")
                article_content = scrape_article_text(link, proxy_config=proxy_config, source_config=source)

                content_to_use = ""
                origin = ""
                if article_content and len(article_content) >= 150:
                    content_to_use = article_content[:2000]
                    origin = "HTML"
                elif rss_summary or rss_title:
                    if rss_title and rss_summary:
                        content_to_use = f"{rss_title}. {rss_summary}"
                    else:
                        content_to_use = rss_summary or rss_title
                    origin = "RSS fallback"
                    fallback_count += 1

                if content_to_use:
                    print(f"  -> Extracted {len(content_to_use)} chars [{origin}]")
                    source_text += f"Article URL: {link}\n{content_to_use}...\n\n"
                    collected_chars += len(content_to_use)
                    collected_articles += 1
                else:
                    print(f"  -> No usable content for {link}")

            all_news_text += source_text
            scraping_stats.append({
                'name': source_name,
                'found': len(articles),
                'collected': collected_articles,
                'fallbacks': fallback_count,
                'chars': collected_chars
            })
            print(f"Source {source_name} complete: {collected_articles}/{len(articles)} articles ({collected_chars} chars, {fallback_count} RSS fallbacks)")

        print("\n" + "=" * 60)
        print("SCRAPING SUMMARY BEFORE GEMINI")
        print("=" * 60)
        total_all_chars = sum(s['chars'] for s in scraping_stats)
        total_all_articles = sum(s['collected'] for s in scraping_stats)
        for s in scraping_stats:
            print(f"- {s['name']:<24}: {s['collected']}/{s['found']} articles ({s['chars']} chars, {s['fallbacks']} fallbacks)")
        print(f"TOTAL: {total_all_articles} articles, {total_all_chars} characters ready for Gemini")
        print("=" * 60 + "\n")

        if total_all_chars == 0:
            print("Warning: All news sources returned 0 content! Adding fallback context.")
            all_news_text = f"Veille thématique et synthèses d'actualités sur : {podcast_theme}."

        # Verify Gemini API Key
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            return {
                'statusCode': 400,
                'body': json.dumps('GEMINI_API_KEY environment variable is not set. Please provide it.')
            }

        print(f"Calling Gemini ({script_model}) to synthesize podcast script and episode description...")
        client = genai.Client(api_key=gemini_api_key)

        prompt = f"""
Vous êtes le rédacteur en chef et la voix de l'émission de podcast "{podcast_title}".
Présentez-vous en tant que "{podcast_host}".
Vous animez un podcast passionnant, expert et dynamique dédié au thème : {podcast_theme}.
Date de l'édition : {datetime.now().strftime("%d/%m/%Y")}
Jour : {datetime.now().strftime("%A")}

Lisez les actualités et informations extraites ci-dessous et produisez deux éléments distincts via la structure JSON demandée :

1. LE SCRIPT AUDIO DU PODCAST ("script") :
   - Présentez-vous en tant que "{podcast_host}".
   - Le podcast doit durer environ {podcast_duration}, être cohérent, fluide, dynamique et rédigé en {podcast_language}.
   - Structurez le script avec une introduction accrocheuse, un développement clair des points essentiels et une conclusion inspirante.
   - Citez naturellement les sources et médias au cours du discours pour renforcer la crédibilité.
   - Chaque sujet doit être séparé par un saut de paragraphe.
   - CONTRAINTES STRICTES DE FORMATAGE DU SCRIPT (À RESPECTER ABSOLUMENT) :
     * Contenu exclusif : Le texte généré doit contenir UNIQUEMENT les paroles prononcées au micro par {podcast_host}.
     * Interdiction formelle : Il est strictement interdit d'insérer des balises, des crochets, des indications de scène, des mentions de jingle, de musique ou de bruitages (ex: pas de "[Musique]", "[Transition]", "[Jingle]", etc.).
     * Les transitions doivent être 100% verbales et intégrées au discours oral.

2. LA DESCRIPTION SYNTHÉTIQUE DE L'ÉPISODE ("description") :
   - Rédigez une description soignée pour la fiche de l'épisode destinée au flux RSS et aux plateformes de podcasts (Spotify, Apple Podcasts, etc.).
   - CONTRAINTES DE FORMATAGE (FLUX RSS) :
     * Le contenu doit être OBLIGATOIREMENT encapsulé dans un bloc CDATA : <![CDATA[ ... ]]>
     * Utilisez uniquement des balises HTML légères : des paragraphes <p>...</p> et des liens hypertextes <a href="URL">Nom du média / titre</a>.
     * N'utilisez AUCUNE syntaxe Markdown (aucun crochet ou parenthèse pour les liens, utilisez impérativement <a href="url">texte</a>).
   - Structure attendue à l'intérieur du bloc CDATA :
     * Un paragraphe <p> d'accroche résumant l'édition du jour.
     * Un ou plusieurs paragraphes <p> décrivant les principaux thèmes abordés.
     * Des paragraphes <p> listant les sources réelles avec leurs liens cliquables HTML (<a href="URL">Nom du média - Titre de l'article</a>) vers les articles réellement traités dans l'épisode.

Voici les actualités extraites :
{all_news_text}
"""

        script_response = client.models.generate_content(
            model=script_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PodcastResponse,
            )
        )

        try:
            parsed_output = json.loads(script_response.text)
            script_text = parsed_output.get("script", "").strip()
            raw_description = parsed_output.get("description", "").strip()
            description_text = format_rss_description(
                raw_description,
                fallback_title=f"{podcast_title} - {datetime.now().strftime('%d/%m/%Y')}"
            )
        except Exception as parse_err:
            print(f"Failed to parse JSON structured output: {parse_err}. Falling back to raw text.")
            script_text = script_response.text
            description_text = format_rss_description(
                "",
                fallback_title=f"{podcast_title} - {datetime.now().strftime('%d/%m/%Y')}"
            )

        if not script_text:
            raise Exception("Script text returned by Gemini is empty.")

        s3_bucket = event.get('s3_bucket') or os.environ.get("S3_BUCKET_NAME")
        if not s3_bucket:
            return {
                'statusCode': 400,
                'body': json.dumps('S3_BUCKET_NAME environment variable is not set.')
            }

        s3_client = boto3.client('s3')
        date_str = datetime.now().strftime('%Y-%m-%d')
        script_file_name = f"script_{date_str}.md"
        sources_file_name = f"sources_{date_str}.md"
        script_key = f"scripts/{script_file_name}"
        sources_key = f"sources/{sources_file_name}"

        print(f"Uploading script to S3 bucket {s3_bucket} as {script_key}...")
        try:
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=script_key,
                Body=script_text.encode('utf-8'),
                ContentType='text/markdown; charset=utf-8'
            )
            print(f"Script uploaded successfully as {script_key}")
        except Exception as e:
            print(f"Failed to upload script to S3: {e}")
            raise Exception(f"Failed to upload script to S3: {e}")

        print(f"Uploading sources description to S3 as {sources_key}...")
        try:
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=sources_key,
                Body=description_text.encode('utf-8'),
                ContentType='text/html; charset=utf-8'
            )
            print(f"Sources description uploaded successfully as {sources_key}")
        except Exception as e:
            print(f"Warning: Failed to upload sources description to S3: {e}")

        print("Splitting text into chunks for TTS synthesis...")

        def chunk_script(text, max_chars=1500):
            paragraphs = text.split('\n\n')
            chunks = []
            current_chunk = ""
            for p in paragraphs:
                if len(current_chunk) + len(p) < max_chars:
                    current_chunk += p + "\n\n"
                else:
                    if not current_chunk.strip() and len(p) >= max_chars:
                        chunks.append(p)
                        current_chunk = ""
                    else:
                        if current_chunk.strip():
                            chunks.append(current_chunk.strip())
                        current_chunk = p + "\n\n"
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            return chunks

        script_chunks = chunk_script(script_text)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pcm_file_path = f"/tmp/raw_audio_{timestamp}.pcm"
        mp3_file_path = f"/tmp/podcast_{timestamp}.mp3"
        file_name = f"podcast_{timestamp}.mp3"

        audio_generated = False
        with open(pcm_file_path, "wb") as f_pcm:
            for i, chunk in enumerate(script_chunks):
                print(f"Generating audio for chunk {i+1}/{len(script_chunks)} with voice '{tts_voice}'...")
                try:
                    audio_response = client.models.generate_content(
                        model=tts_model,
                        contents=chunk,
                        config=types.GenerateContentConfig(
                            response_modalities=["AUDIO"],
                            speech_config=types.SpeechConfig(
                                voice_config=types.VoiceConfig(
                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                        voice_name=tts_voice,
                                    )
                                )
                            ),
                        )
                    )

                    if audio_response.candidates and audio_response.candidates[0].content.parts:
                        for part in audio_response.candidates[0].content.parts:
                            if part.inline_data:
                                f_pcm.write(part.inline_data.data)
                                audio_generated = True
                                break
                except Exception as chunk_err:
                    print(f"Error generating chunk {i+1}: {chunk_err}")
                    raise

        if audio_generated:
            print("Converting PCM to MP3 using FFmpeg...")
            try:
                process = subprocess.Popen(
                    [
                        '/opt/bin/ffmpeg',
                        '-y',
                        '-f', 's16le',
                        '-ar', '24000',
                        '-ac', '1',
                        '-i', pcm_file_path,
                        '-f', 'mp3',
                        '-b:a', '128k',
                        mp3_file_path
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout, stderr = process.communicate()

                if process.returncode != 0:
                    raise Exception(f"FFmpeg conversion failed: {stderr.decode('utf-8')}")

                print(f"Uploading audio to S3 bucket: {s3_bucket} as {file_name}")
                s3_client = boto3.client('s3')
                with open(mp3_file_path, "rb") as f_mp3:
                    s3_client.put_object(
                        Bucket=s3_bucket,
                        Key=file_name,
                        Body=f_mp3,
                        ContentType='audio/mpeg'
                    )

                # Cleanup /tmp
                if os.path.exists(pcm_file_path):
                    os.remove(pcm_file_path)
                if os.path.exists(mp3_file_path):
                    os.remove(mp3_file_path)

                episode_title = f"{podcast_title} - {datetime.now().strftime('%d/%m/%Y')}"
                return {
                    'statusCode': 200,
                    's3_bucket': s3_bucket,
                    's3_key': file_name,
                    'script_key': script_key,
                    's3_script_key': script_key,
                    's3_sources_key': sources_key,
                    'title': episode_title,
                    'timestamp': timestamp,
                    'description': description_text,
                    'body': json.dumps({
                        'message': f"Podcast '{podcast_title}' successfully generated and uploaded.",
                        's3_bucket': s3_bucket,
                        's3_key': file_name,
                        'script_key': script_key,
                        's3_script_key': script_key,
                        's3_sources_key': sources_key,
                        'title': episode_title,
                        'timestamp': timestamp,
                        'description': description_text
                    })
                }
            except Exception as ffmpeg_err:
                print(f"FFmpeg error: {ffmpeg_err}")
                raise Exception(f"Failed to convert or upload audio: {ffmpeg_err}")
        else:
            raise Exception("Gemini TTS did not return audio data in its modalities.")
    except Exception as e:
        print(f"Error processing podcast: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
