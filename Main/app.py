import json
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin, urlparse

import boto3
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def fetch_url(url, proxy_config=None, source_config=None):
    use_proxy = proxy_config.get('enabled', False) if proxy_config else False
    if source_config and 'use_proxy' in source_config:
        use_proxy = source_config['use_proxy']
        
    if use_proxy and proxy_config:
        endpoint = os.environ.get('PROXY_ENDPOINT') or proxy_config.get('endpoint', '')
        api_key = os.environ.get('PROXY_API_KEY') or proxy_config.get('api_key', '')
        response = requests.get(endpoint, params={'key': api_key, 'url': url}, timeout=15)
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

def get_links_from_rss_feed(source_config, proxy_config=None):
    url = source_config['url']
    base_domain = source_config.get('base_domain', '')
    max_links = source_config.get('max_articles', 3)
    
    try:
        response = fetch_url(url, proxy_config=proxy_config, source_config=source_config)
        content = response.content
        
        links = []
        exclude_keywords = ['login', 'register', 'connexion', 'abonnement', 'inscription', 'compte', 'newsletter']
        
        # 1. Try parsing using xml.etree.ElementTree
        try:
            root = ET.fromstring(content)
            for elem in root.iter():
                tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                if tag.lower() == 'item':
                    link_text = None
                    for child in elem:
                        child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                        if child_tag.lower() == 'link':
                            link_text = (child.text or child.get('href') or '').strip()
                            if link_text:
                                break
                    if not link_text:
                        for child in elem:
                            child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                            if child_tag.lower() == 'guid':
                                candidate = (child.text or '').strip()
                                if candidate.startswith('http://') or candidate.startswith('https://'):
                                    link_text = candidate
                                    break
                    if link_text:
                        full_url = urljoin(url, link_text)
                        parsed = urlparse(full_url)
                        if (not base_domain or base_domain in parsed.netloc) and parsed.scheme in ('http', 'https'):
                            if not any(kw in full_url.lower() for kw in exclude_keywords):
                                if full_url not in links:
                                    links.append(full_url)
                elif tag.lower() == 'entry':  # Atom feed
                    link_text = None
                    for child in elem:
                        child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                        if child_tag.lower() == 'link':
                            candidate = (child.get('href') or child.text or '').strip()
                            if candidate:
                                rel = child.get('rel', 'alternate')
                                if rel == 'alternate' or not link_text:
                                    link_text = candidate
                    if link_text:
                        full_url = urljoin(url, link_text)
                        parsed = urlparse(full_url)
                        if (not base_domain or base_domain in parsed.netloc) and parsed.scheme in ('http', 'https'):
                            if not any(kw in full_url.lower() for kw in exclude_keywords):
                                if full_url not in links:
                                    links.append(full_url)
                if len(links) >= max_links:
                    break
        except Exception as xml_err:
            print(f"ElementTree parsing failed for RSS feed {url}, falling back to BeautifulSoup: {xml_err}")

        # 2. Fallback to BeautifulSoup if ElementTree found nothing or errored
        if not links:
            soup = BeautifulSoup(content, 'html.parser')
            for item in soup.find_all(['item', 'entry']):
                link_tag = item.find('link')
                link_text = None
                if link_tag:
                    link_text = link_tag.get('href') or link_tag.get_text(strip=True)
                    if not link_text and link_tag.next_sibling:
                        sibling_text = str(link_tag.next_sibling).strip()
                        if sibling_text.startswith('http'):
                            link_text = sibling_text
                if not link_text:
                    guid = item.find('guid')
                    if guid and guid.get_text(strip=True).startswith('http'):
                        link_text = guid.get_text(strip=True)
                
                if link_text:
                    full_url = urljoin(url, link_text)
                    parsed = urlparse(full_url)
                    if (not base_domain or base_domain in parsed.netloc) and parsed.scheme in ('http', 'https'):
                        if not any(kw in full_url.lower() for kw in exclude_keywords):
                            if full_url not in links:
                                links.append(full_url)
                if len(links) >= max_links:
                    break

        return links[:max_links]
    except Exception as e:
        print(f"Error fetching/parsing RSS feed {url}: {e}")
        return []

def scrape_article_text(url, proxy_config=None, source_config=None):
    try:
        response = fetch_url(url, proxy_config=proxy_config, source_config=source_config)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40])
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
        for source in config.get('sources', []):
            print(f"Processing source: {source['name']}")
            
            is_rss = source.get('isRss', False)
            if isinstance(is_rss, str):
                is_rss = is_rss.lower() == 'true'
                
            if is_rss:
                article_links = get_links_from_rss_feed(source, proxy_config)
            else:
                article_links = get_links_from_front_page(source, proxy_config)
            
            source_text = f"Source : {source['name']}\n"
            for link in article_links:
                print(f"Scraping article: {link}")
                article_content = scrape_article_text(link, proxy_config=proxy_config, source_config=source)
                if article_content:
                    source_text += f"Article URL: {link}\n{article_content[:2000]}...\n\n"
            
            all_news_text += source_text

        if not all_news_text.strip():
            print("Warning: No articles extracted from sources. Adding fallback context.")
            all_news_text = f"Veille thématique et synthèses d'actualités sur : {podcast_theme}."
            
        # Verify Gemini API Key
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            return {
                'statusCode': 400,
                'body': json.dumps('GEMINI_API_KEY environment variable is not set.')
            }
            
        print(f"Calling Gemini ({script_model}) to synthesize thematic podcast script...")
        client = genai.Client(api_key=gemini_api_key)
        
        prompt = f"""
Se présenter en tant que "{podcast_host}". Vous êtes l'animateur/animatrice d'un podcast passionnant et expert dédié au thème : {podcast_theme}.

Identité du podcast : "{podcast_title}"
Date de l'édition : {datetime.now().strftime("%d/%m/%Y")}
Jour : {datetime.now().strftime("%A")}

Instructions de rédaction :
- Rédigez un script de podcast complet, captivant et structuré autour du thème "{podcast_theme}".
- Utilisez les articles, actualités et synthèses ci-dessous comme matière première.
- Le podcast doit durer environ {podcast_duration}, être fluide, dynamique et rédigé en {podcast_language}.
- Décryptez les enjeux, donnez des explications claires et citez les sources pour renforcer la pertinence et la crédibilité.
- Structurez l'émission :
  1. Introduction énergique présentant l'épisode et le sommaire des sujets.
  2. Corps de l'émission avec des transitions naturelles et des analyses de fond.
  3. Conclusion inspirante avec ouverture et appel à suivre les prochains épisodes.

CONTRAINTES STRICTES DE FORMATAGE (À RESPECTER ABSOLUMENT) :
- Contenu exclusif : Le texte généré doit contenir UNIQUEMENT les paroles prononcées au micro par {podcast_host}.
- Interdiction formelle de métadonnées : Pas de crochets, de balises, d'indications scéniques, de mentions de musique ou de jingles (ex: AUCUN "[Musique]", "[Transition]", "[Jingle]", etc.).
- Les transitions doivent être 100% verbales et intégrées au discours oral.

Voici les informations et articles extraits :
{all_news_text}
"""
        
        script_response = client.models.generate_content(
            model=script_model,
            contents=prompt
        )
        script_text = script_response.text

        s3_bucket = event.get('s3_bucket') or os.environ.get("S3_BUCKET_NAME")
        if not s3_bucket:
            return {
                'statusCode': 400,
                'body': json.dumps('S3_BUCKET_NAME environment variable is not set.')
            }

        print(f"Uploading script to S3 bucket {s3_bucket}...")
        try:
            s3_client = boto3.client('s3')
            date_str = datetime.now().strftime('%Y-%m-%d')
            script_file_name = f"script_{date_str}.md"
            script_key = f"scripts/{script_file_name}"
            
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=script_key,
                Body=script_text.encode('utf-8'),
                ContentType='text/markdown'
            )
            print(f"Script uploaded successfully as {script_key}")
        except Exception as e:
            print(f"Failed to upload script to S3: {e}")
            raise Exception(f"Failed to upload script to S3: {e}")

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
                        continue

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
                    'title': episode_title,
                    'timestamp': timestamp,
                    'body': json.dumps({
                        'message': f"Podcast '{podcast_title}' successfully generated and uploaded.",
                        's3_bucket': s3_bucket,
                        's3_key': file_name,
                        'script_key': script_key,
                        'title': episode_title,
                        'timestamp': timestamp
                    })
                }
            except Exception as ffmpeg_err:
                print(f"FFmpeg error: {ffmpeg_err}")
                raise Exception(f"Failed to convert or upload audio: {ffmpeg_err}")
        else:
            raise Exception("Gemini TTS did not return audio data.")
    except Exception as e:
        print(f"Error processing podcast: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
