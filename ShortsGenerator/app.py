import json
import os
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
    summary_model = os.environ.get("GEMINI_SUMMARY_MODEL", "gemini-2.5-flash")
    image_model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    host_image_style = os.environ.get(
        "HOST_IMAGE_STYLE",
        "un animateur de podcast tech jeune et dynamique dans un studio moderne et lumineux avec microphone professionnel"
    )

    try:
        bucket = None
        short_file_name = None
        timestamp = None

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
Vous êtes un expert en création de contenu court et viral pour les réseaux sociaux (TikTok, YouTube Shorts, Instagram Reels).
Voici le script d'un épisode du podcast "{podcast_title}".
Résumez-le en un script très court, percutant et dynamique (1 minute parlée, soit environ 150 à 200 mots).
Ce résumé doit accrocher immédiatement l'audience et l'inviter à écouter l'épisode complet.

Mentionnez que le podcast complet est "{podcast_title}" disponible sur Spotify.

CONTRAINTES STRICTES DE FORMATAGE (À RESPECTER ABSOLUMENT) :
- Contenu exclusif : Le texte généré doit contenir uniquement les paroles prononcées par la voix off.
- Aucun crochet, aucune indication de montage ou d'effet sonore.

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
            
            # Upload short markdown script
            print(f"Uploading short script {short_text_name} to {bucket}")
            s3_client.put_object(
                Bucket=bucket,
                Key=f"shorts/{short_text_name}",
                Body=summary_text.encode('utf-8'),
                ContentType='text/markdown'
            )

            # Generate and upload the daily illustration image
            print(f"Generating podcast illustration using {image_model}...")
            try:
                image_prompt = os.environ.get(
                    "IMAGE_PROMPT_TEMPLATE",
                    f"Génère une image d'illustration de podcast au format carré 1024x1024 pixels, style photoréaliste, vue de 3/4 en studio de podcast moderne. {host_image_style}. Expression enthousiaste et captivante. IMPORTANT: pas de texte incrusté, uniquement des illustrations visuelles traitant des sujets suivants : {summary_text[:1000]}"
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
                        Key="shorts/" + image_file_name,
                        Body=image_bytes,
                        ContentType=mime_type
                    )
            except Exception as img_err:
                print(f"Failed to generate or upload illustration image: {img_err}")

        # Start Video Step Function if ARN is provided
        sfn_arn = os.environ.get("STEP_FUNCTION_ARN")
        bucket = event.get('s3_bucket') or os.environ.get("S3_BUCKET_NAME")
        if sfn_arn and bucket:
            print(f"Starting Video Step Function execution: {sfn_arn}")
            sfn_input = {
                "bucket": bucket,
                "key": f"shorts/short_script_{datetime.now().strftime('%Y-%m-%d')}.md"
            }
            sf_client.start_execution(
                stateMachineArn=sfn_arn,
                input=json.dumps(sfn_input)
            )

        return {'statusCode': 200, 'body': 'Shorts successfully generated.'}
    except Exception as e:
        print(f"Error: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
