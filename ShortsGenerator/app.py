import json
import os
import subprocess
import urllib.parse
from datetime import datetime

import boto3
from google import genai
from google.genai import types


def handler(event, context):
    print("Received event: " + json.dumps(event, indent=2))

    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        return {'statusCode': 400, 'body': 'GEMINI_API_KEY not set'}

    s3_client = boto3.client('s3')
    client = genai.Client(api_key=gemini_api_key)
    sf_client = boto3.client('stepfunctions')

    podcast_title = os.environ.get("PODCAST_TITLE", "Tech & IA Horizon")
    summary_model = os.environ.get("GEMINI_SUMMARY_MODEL", "gemini-3.8-flash")
    tts_model = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
    tts_voice = os.environ.get("GEMINI_TTS_VOICE", "Laomedeia")
    image_model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    host_image_style = os.environ.get(
        "HOST_IMAGE_STYLE",
        "un animateur de podcast tech jeune et dynamique dans son studio moderne ou sur le terrain avec microphone professionnel, visage expressif et souriant"
    )

    pcm_file_path = None
    mp3_file_path = None
    short_file_name = None
    short_text_name = None
    image_file_name = None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        bucket = None

        # Support both S3 trigger events and direct payload from Step Functions
        records = event.get('Records', None)
        tasks = []
        if not records:
            bucket = event.get('s3_bucket') or os.environ.get("S3_BUCKET_NAME")
            key = event.get('script_key') or event.get('s3_script_key') or event.get('s3_key')
            if key and not key.endswith('.md'):
                # Derive script key from today's date if not a markdown file
                date_str = datetime.now().strftime('%Y-%m-%d')
                key = f"scripts/script_{date_str}.md"
            if bucket and key:
                tasks.append({'bucket': bucket, 'key': key})
        else:
            for record in records:
                b = record['s3']['bucket']['name']
                k = urllib.parse.unquote_plus(record['s3']['object']['key'], encoding='utf-8')
                if k.endswith('.md') or 'script' in k:
                    tasks.append({'bucket': b, 'key': k})

        for task in tasks:
            bucket = task['bucket']
            key = task['key']
            print(f"Processing script: {key} from bucket: {bucket}")

            # Download the script
            response = s3_client.get_object(Bucket=bucket, Key=key)
            script_text = response['Body'].read().decode('utf-8')

            print(f"Generating summary for Shorts using {summary_model}...")
            prompt = f"""
Transforme le script de podcast d'actualités fourni en un script vidéo ultra-court et percutant de 30 à 45 secondes (soit environ 70 à 100 mots).

Consignes de rédaction :
- Accroche immédiate : Rentre directement dans le sujet dès la première seconde.
- Contenu : Sélectionne et résume uniquement 1 ou 2 actualités marquantes, sans fioritures.
- Appel à l'action final : Termine par une phrase incisive invitant à écouter la suite et les détails sur "{podcast_title}" sur Spotify.

CONTRAINTES STRICTES DE FORMATAGE (À RESPECTER ABSOLUMENT) :
- Contenu exclusif : Le texte généré doit contenir UNIQUEMENT les paroles prononcées par la voix off.
- Interdiction formelle : Aucun crochet, aucune mention de jingle, de musique ou d'effet sonore.

Script original :
{script_text}
"""
            summary_response = client.models.generate_content(
                model=summary_model,
                contents=prompt
            )
            summary_text = summary_response.text

            date_of_the_day = datetime.now().strftime("%Y-%m-%d")
            base_key_name = os.path.basename(key)
            short_text_name = f"short_{base_key_name}"

            # 1. Upload short markdown script
            print(f"Uploading short script {short_text_name} to {bucket}")
            s3_client.put_object(
                Bucket=bucket,
                Key=f"shorts/{short_text_name}",
                Body=summary_text.encode('utf-8'),
                ContentType='text/markdown; charset=utf-8'
            )

            # 2. Generate audio for the short script
            print(f"Generating audio for Shorts using {tts_model} (voice: {tts_voice})...")
            pcm_file_path = f"/tmp/short_raw_{timestamp}.pcm"
            mp3_file_path = f"/tmp/short_{timestamp}.mp3"

            audio_response = client.models.generate_content(
                model=tts_model,
                contents=summary_text,
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

            with open(pcm_file_path, "wb") as f_pcm:
                if audio_response.candidates and audio_response.candidates[0].content.parts:
                    for part in audio_response.candidates[0].content.parts:
                        if part.inline_data:
                            f_pcm.write(part.inline_data.data)
                            break

            print("Converting short PCM to MP3 using FFmpeg...")
            process = subprocess.Popen(
                [
                    '/opt/bin/ffmpeg', '-y',
                    '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', pcm_file_path,
                    '-f', 'mp3', '-b:a', '128k', mp3_file_path
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                raise Exception(f"FFmpeg failed: {stderr.decode('utf-8')}")

            short_file_name = f"{date_of_the_day}_shorts.mp3"
            print(f"Uploading short audio {short_file_name} to {bucket}")
            with open(mp3_file_path, "rb") as f_mp3:
                s3_client.put_object(
                    Bucket=bucket,
                    Key=f"shorts/{short_file_name}",
                    Body=f_mp3,
                    ContentType='audio/mpeg'
                )

            # 3. Generate and upload the daily illustration image
            print(f"Generating podcast illustration using {image_model}...")
            try:
                image_prompt = os.environ.get(
                    "IMAGE_PROMPT_TEMPLATE",
                    f"Génère une image d'illustration de podcast au format carré 1024x1024 pixels, style photoréaliste, vue de 3/4. {host_image_style}. IMPORTANT: pas de texte incrusté, uniquement des illustrations visuelles traitant des sujets suivants : {summary_text[:1000]}"
                )
                image_response = client.models.generate_content(
                    model=image_model,
                    contents=image_prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"]
                    )
                )
                image_bytes = None
                mime_type = 'image/png'
                if image_response.candidates and image_response.candidates[0].content.parts:
                    for part in image_response.candidates[0].content.parts:
                        if part.inline_data:
                            image_bytes = part.inline_data.data
                            if part.inline_data.mime_type:
                                mime_type = part.inline_data.mime_type
                            break

                if image_bytes:
                    ext = "jpg" if "jpeg" in mime_type else "png"
                    image_file_name = f"{date_of_the_day}_shorts.{ext}"
                    print(f"Uploading illustration {image_file_name} to {bucket}")
                    s3_client.put_object(
                        Bucket=bucket,
                        Key=f"shorts/{image_file_name}",
                        Body=image_bytes,
                        ContentType=mime_type
                    )
            except Exception as img_err:
                print(f"Failed to generate or upload illustration image: {img_err}")

        # Start Video Step Function if ARN is provided
        sfn_arn = os.environ.get("STEP_FUNCTION_ARN")
        bucket = bucket or event.get('s3_bucket') or os.environ.get("S3_BUCKET_NAME")
        if sfn_arn and bucket:
            print(f"Starting Video Step Function execution: {sfn_arn}")
            sfn_input = {
                "bucket": bucket,
                "key": f"shorts/{short_text_name}" if short_text_name else f"shorts/short_script_{datetime.now().strftime('%Y-%m-%d')}.md"
            }
            sf_client.start_execution(
                stateMachineArn=sfn_arn,
                input=json.dumps(sfn_input)
            )

        return {
            'statusCode': 200,
            's3_bucket': bucket,
            's3_key': f"shorts/{short_file_name}" if short_file_name else None,
            's3_audio_key': f"shorts/{short_file_name}" if short_file_name else None,
            's3_script_key': f"shorts/{short_text_name}" if short_text_name else None,
            's3_image_key': f"shorts/{image_file_name}" if image_file_name else None,
            'timestamp': timestamp,
            'body': json.dumps({
                'message': 'Shorts successfully generated.',
                's3_bucket': bucket,
                's3_key': f"shorts/{short_file_name}" if short_file_name else None,
                's3_audio_key': f"shorts/{short_file_name}" if short_file_name else None,
                's3_script_key': f"shorts/{short_text_name}" if short_text_name else None,
                's3_image_key': f"shorts/{image_file_name}" if image_file_name else None,
                'timestamp': timestamp
            })
        }
    except Exception as e:
        print(f"Error in ShortsGenerator: {e}")
        raise e
    finally:
        if pcm_file_path and os.path.exists(pcm_file_path):
            try:
                os.remove(pcm_file_path)
            except Exception:
                pass
        if mp3_file_path and os.path.exists(mp3_file_path):
            try:
                os.remove(mp3_file_path)
            except Exception:
                pass
