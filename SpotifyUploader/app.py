import os
import json
import base64
import subprocess
import boto3
import re
from datetime import datetime

def get_french_date_title(event):
    timestamp = event.get('timestamp')
    s3_key = event.get('s3_key') or ''
    
    date_obj = None
    
    # 1. Try to parse timestamp
    if timestamp:
        clean_ts = re.split(r'[_ ]', str(timestamp))[0]
        for fmt in ('%Y%m%d', '%Y-%m-%d'):
            try:
                date_obj = datetime.strptime(clean_ts, fmt)
                break
            except Exception:
                pass
                
    # 2. Try to parse s3_key if date_obj not found yet
    if not date_obj and s3_key:
        match = re.search(r'\d{8}', s3_key)
        if match:
            try:
                date_obj = datetime.strptime(match.group(0), '%Y%m%d')
            except Exception:
                pass
                
    # 3. Fallback to current date
    if not date_obj:
        date_obj = datetime.now()
        
    french_months = {
        1: "Janvier",
        2: "Février",
        3: "Mars",
        4: "Avril",
        5: "Mai",
        6: "Juin",
        7: "Juillet",
        8: "Août",
        9: "Septembre",
        10: "Octobre",
        11: "Novembre",
        12: "Décembre"
    }
    
    day = date_obj.day
    month = french_months.get(date_obj.month, "")
    year = date_obj.year
    
    podcast_title = os.environ.get('PODCAST_TITLE', 'Tech & IA Horizon')
    return f"{podcast_title} - {day} {month} {year}"

def get_secret(secret_name):
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager')
    try:
        response = client.get_secret_value(SecretId=secret_name)
        if 'SecretString' in response:
            return json.loads(response['SecretString'])
    except Exception as e:
        print(f"Error retrieving secret {secret_name}: {e}")
        raise e
    return None

def handler(event, context):
    print("Received event: " + json.dumps(event, indent=2))
    
    # 1. Extract bucket, key, and metadata from input
    s3_bucket = event.get('s3_bucket') or os.environ.get('S3_BUCKET_NAME')
    s3_key = event.get('s3_key') # e.g. "podcast_20260526_120000.mp3"
    episode_title = event.get('title') or get_french_date_title(event)
    episode_description = event.get('description') or os.environ.get(
        'SPOTIFY_EPISODE_DESCRIPTION',
        "Votre épisode dédié aux dernières innovations, à l'intelligence artificielle et aux technologies de demain."
    )
    
    if not s3_key:
        return {'statusCode': 400, 'body': 's3_key is required in the input event.'}

    if not s3_bucket:
        return {'statusCode': 400, 'body': 's3_bucket is required (set S3_BUCKET_NAME or pass in event).'}

    # 2. Spotify Show ID validation
    show_id = event.get('show_id') or os.environ.get('SPOTIFY_SHOW_ID')
    if not show_id:
        return {
            'statusCode': 400,
            'body': 'SPOTIFY_SHOW_ID environment variable is not set. Please provide a valid Spotify Show ID (e.g. spotify:show:xxxxxxxxxxxx).'
        }

    # 3. Retrieve Spotify credentials from AWS Secrets Manager
    secret_name = os.environ.get('SPOTIFY_SECRET_NAME', 'thematic-podcast-SpotifyCredentials')
    secrets = get_secret(secret_name)
    
    if not secrets:
        return {
            'statusCode': 500,
            'body': f"Failed to retrieve secret '{secret_name}' from Secrets Manager (returned empty or None)."
        }
        
    if not isinstance(secrets, dict):
        return {
            'statusCode': 500,
            'body': f"Secret '{secret_name}' was loaded but is not a valid JSON object. It must contain 'token_b64' and 'dpop_key_b64'."
        }
        
    missing_keys = [k for k in ['token_b64', 'dpop_key_b64'] if k not in secrets]
    if missing_keys:
        found_keys = list(secrets.keys())
        return {
            'statusCode': 500,
            'body': f"Secret '{secret_name}' is missing required keys: {', '.join(missing_keys)}. Found keys: {', '.join(found_keys)}."
        }
        
    token_b64 = secrets['token_b64']
    dpop_key_b64 = secrets['dpop_key_b64']
    
    # 4. Recreate credentials directory in /tmp
    config_dir = "/tmp/save-to-spotify"
    os.makedirs(config_dir, exist_ok=True)
    
    token_path = os.path.join(config_dir, "token.json")
    dpop_key_path = os.path.join(config_dir, "dpop_key.json")
    
    with open(token_path, "wb") as f_tok:
        f_tok.write(base64.b64decode(token_b64))
        
    with open(dpop_key_path, "wb") as f_dpop:
        f_dpop.write(base64.b64decode(dpop_key_b64))
        
    # Configure env var so CLI reads from /tmp
    os.environ["XDG_CONFIG_HOME"] = "/tmp"
    
    # 5. Download MP3 from S3 to /tmp
    s3_client = boto3.client('s3')
    local_mp3_path = f"/tmp/{os.path.basename(s3_key)}"
    
    print(f"Downloading s3://{s3_bucket}/{s3_key} to {local_mp3_path}...")
    s3_client.download_file(s3_bucket, s3_key, local_mp3_path)
    
    # 6. Execute save-to-spotify upload CLI
    cmd = [
        "/opt/bin/save-to-spotify",
        "upload",
        local_mp3_path,
        "--title", episode_title,
        "--summary", episode_description,
        "--show-id", show_id
    ]
    
    print(f"Executing command: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("CLI Output:", result.stdout)
        print("CLI Error Output:", result.stderr)
    except subprocess.CalledProcessError as e:
        print("CLI command failed with exit code:", e.returncode)
        print("CLI Output:", e.stdout)
        print("CLI Error Output:", e.stderr)
        raise Exception(f"Spotify upload failed: {e.stderr}")
    finally:
        if os.path.exists(local_mp3_path):
            os.remove(local_mp3_path)
            
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Episode successfully uploaded to Spotify.',
            's3_key': s3_key,
            'title': episode_title,
            'show_id': show_id
        })
    }
