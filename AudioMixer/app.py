import json
import os
import random
import shutil
import subprocess
import urllib.parse
from datetime import datetime
import boto3


def get_audio_duration(file_path):
    """
    Returns duration of the audio file in seconds using ffprobe.
    """
    ffprobe_bin = shutil.which('ffprobe') or '/opt/bin/ffprobe'
    cmd = [
        ffprobe_bin,
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    duration_str = result.stdout.strip()
    return float(duration_str)


def list_music_tracks(s3_client, bucket):
    """
    Finds all music tracks under 'ressources/music/' (and fallback 'resources/music/')
    """
    prefixes_to_check = ['ressources/music/', 'resources/music/', 'ressources/music', 'resources/music']
    valid_extensions = ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg')

    music_keys = []

    for prefix in prefixes_to_check:
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj.get('Key', '')
                size = obj.get('Size', 0)
                if size > 0 and key.lower().endswith(valid_extensions):
                    if key not in music_keys:
                        music_keys.append(key)
        if music_keys:
            break

    return music_keys


def handler(event, context):
    print("Received event: " + json.dumps(event, indent=2))

    s3_client = boto3.client('s3')

    # 1. Extract bucket, key, and metadata from input
    s3_bucket = event.get('s3_bucket') or os.environ.get('S3_BUCKET_NAME')
    s3_key = event.get('s3_key')
    timestamp = event.get('timestamp')
    title = event.get('title')
    description = event.get('description')
    script_key = event.get('script_key')
    s3_script_key = event.get('s3_script_key') or script_key
    s3_sources_key = event.get('s3_sources_key')

    # Handle S3 Records event fallback
    records = event.get('Records', None)
    if records:
        for record in records:
            s3_bucket = record['s3']['bucket']['name']
            s3_key = urllib.parse.unquote_plus(record['s3']['object']['key'], encoding='utf-8')
            break

    if not s3_bucket:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 's3_bucket is required in event or S3_BUCKET_NAME env var.'})
        }

    if not s3_key:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 's3_key is required in event.'})
        }

    if not timestamp:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"Target Voice File: s3://{s3_bucket}/{s3_key}")

    # 2. Find music tracks in /ressources/music/
    music_tracks = list_music_tracks(s3_client, s3_bucket)
    if not music_tracks:
        print(f"⚠️ No music tracks found under 'ressources/music/' in S3 bucket '{s3_bucket}'. Passing through voice track without mixing.")
        return {
            'statusCode': 200,
            's3_bucket': s3_bucket,
            's3_key': s3_key,
            'timestamp': timestamp,
            'title': title,
            'description': description,
            'script_key': script_key,
            's3_script_key': s3_script_key,
            's3_sources_key': s3_sources_key,
            'music_track': None,
            'voice_duration': None,
            'body': json.dumps({
                'message': f'No background music found under ressources/music/ in {s3_bucket}. Voice track preserved without mixing.',
                's3_bucket': s3_bucket,
                's3_key': s3_key,
                'timestamp': timestamp,
                'title': title,
                'description': description,
                'script_key': script_key,
                's3_script_key': s3_script_key,
                's3_sources_key': s3_sources_key
            })
        }

    chosen_music_key = random.choice(music_tracks)
    print(f"Selected background music: s3://{s3_bucket}/{chosen_music_key} (from {len(music_tracks)} available tracks)")

    # Paths in /tmp
    voice_filename = os.path.basename(s3_key)
    music_filename = os.path.basename(chosen_music_key)

    local_voice_path = f"/tmp/voice_{timestamp}_{voice_filename}"
    local_music_path = f"/tmp/music_{timestamp}_{music_filename}"
    local_output_path = f"/tmp/mixed_{timestamp}_{voice_filename}"

    try:
        # 3. Download files from S3
        print(f"Downloading voice file: s3://{s3_bucket}/{s3_key} -> {local_voice_path}")
        s3_client.download_file(s3_bucket, s3_key, local_voice_path)

        print(f"Downloading music file: s3://{s3_bucket}/{chosen_music_key} -> {local_music_path}")
        s3_client.download_file(s3_bucket, chosen_music_key, local_music_path)

        # 4. Determine duration of voice track
        voice_duration = get_audio_duration(local_voice_path)
        print(f"Voice track duration: {voice_duration:.2f} seconds")

        # Calculate fade out timing (fade out over last 3 seconds of the track)
        fade_duration = 3.0
        if voice_duration <= fade_duration:
            fade_start = 0.0
            fade_duration = voice_duration
        else:
            fade_start = voice_duration - fade_duration

        print(f"Configuring audio mix: fade-in=2s, fade-out start={fade_start:.2f}s (d={fade_duration:.2f}s)")

        # 5. Build and execute ffmpeg command
        ffmpeg_bin = shutil.which('ffmpeg') or '/opt/bin/ffmpeg'

        filter_complex = (
            f"[1:a]volume=0.10,afade=t=in:ss=0:d=2[bg]; "
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=3[mixed]; "
            f"[mixed]afade=t=out:st={fade_start:.2f}:d={fade_duration:.2f}[out]"
        )

        cmd = [
            ffmpeg_bin,
            '-y',
            '-i', local_voice_path,
            '-stream_loop', '-1',
            '-i', local_music_path,
            '-filter_complex', filter_complex,
            '-map', '[out]',
            '-c:a', 'libmp3lame',
            '-q:a', '2',
            local_output_path
        ]

        print(f"Running FFmpeg: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            error_details = stderr.decode('utf-8', errors='replace')
            raise Exception(f"FFmpeg mixing failed with exit code {process.returncode}: {error_details}")

        print("Audio mixing completed successfully.")

        # 6. Upload mixed audio back to S3, overwriting the original file
        print(f"Overwriting original voice file in S3: s3://{s3_bucket}/{s3_key}")
        with open(local_output_path, "rb") as f_out:
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=s3_key,
                Body=f_out,
                ContentType='audio/mpeg'
            )

        print(f"Successfully uploaded mixed podcast to s3://{s3_bucket}/{s3_key}")

        return {
            'statusCode': 200,
            's3_bucket': s3_bucket,
            's3_key': s3_key,
            'timestamp': timestamp,
            'title': title,
            'description': description,
            'script_key': script_key,
            's3_script_key': s3_script_key,
            's3_sources_key': s3_sources_key,
            'music_track': chosen_music_key,
            'voice_duration': round(voice_duration, 2),
            'body': json.dumps({
                'message': f'Background music {chosen_music_key} successfully mixed into {s3_key}. Original file overwritten.',
                's3_bucket': s3_bucket,
                's3_key': s3_key,
                'timestamp': timestamp,
                'title': title,
                'description': description,
                'script_key': script_key,
                's3_script_key': s3_script_key,
                's3_sources_key': s3_sources_key,
                'music_track': chosen_music_key,
                'voice_duration': round(voice_duration, 2)
            })
        }

    except Exception as e:
        print(f"Error mixing audio: {e}")
        raise e
    finally:
        # Cleanup temporary files
        for tmp_file in [local_voice_path, local_music_path, local_output_path]:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
