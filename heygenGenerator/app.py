import json
import os
import random
import time
from urllib.parse import urlparse

import boto3
import requests

def get_presigned_url(s3_client, bucket, key, expiration=3600):
    try:
        response = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=expiration
        )
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return None
    return response

def handler(event, context):
    try:
        s3_bucket = event.get('s3_bucket') or os.environ.get('S3_BUCKET_NAME')
        s3_key = event.get('s3_key')
        
        if not s3_bucket or not s3_key:
            return {
                'statusCode': 400,
                'body': json.dumps('s3_bucket and s3_key must be provided in the event or environment.')
            }

        api_key = os.environ.get("HEYGEN_API_KEY")
        if not api_key:
            return {'statusCode': 400, 'body': 'HEYGEN_API_KEY is not set'}
            
        avatar_id = os.environ.get("HEYGEN_AVATAR_ID")
        cloudfront_domain = os.environ.get("CLOUDFRONT_DOMAIN")

        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }
        
        print(f"Processing MP3 from S3: s3://{s3_bucket}/{s3_key}")
        
        s3_client = boto3.client('s3')
        local_mp3_path = f"/tmp/{os.path.basename(s3_key)}"
        
        print(f"Downloading s3://{s3_bucket}/{s3_key} to {local_mp3_path}")
        s3_client.download_file(s3_bucket, s3_key, local_mp3_path)
        
        # 1. Upload the MP3 to HeyGen to get an asset_id
        print("Uploading MP3 to HeyGen...")
        with open(local_mp3_path, 'rb') as f:
            upload_response = requests.post(
                "https://api.heygen.com/v3/assets",
                headers={"x-api-key": api_key},
                files={"file": f}
            )
            
        if upload_response.status_code != 200:
            raise Exception(f"Failed to upload asset to HeyGen: {upload_response.text}")
            
        asset_id = upload_response.json().get("data", {}).get("asset_id")
        if not asset_id:
            raise Exception("No asset_id returned from HeyGen")
            
        print(f"Asset uploaded successfully. Asset ID: {asset_id}")
        
        # 2. Select Avatar ID
        if not avatar_id:
            avatar_ids = ["40d6af725bbd44e3aa47a7f6d88fd0dc", "987a73da44534fdcbcbb7373f3cdbea4", "4b2a7ec05d614f2ab19e3ab796b97f88"]
            avatar_id = random.choice(avatar_ids)

        if cloudfront_domain:
            audio_url = f"https://{cloudfront_domain}/{s3_key}"
        else:
            audio_url = get_presigned_url(s3_client, s3_bucket, s3_key)

        print(f"Initiating avatar video generation for Avatar ID: {avatar_id}")
        generate_payload = {
            "dimension": {
                "width": 1080,
                "height": 1920
            },
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "scale": 1,
                        "avatar_style": "normal",
                        "talking_style": "stable",
                        "avatar_id": avatar_id
                    },
                    "voice": {
                        "speed": 1,
                        "pitch": 0,
                        "duration": "1",
                        "type": "audio",
                        "audio_url": audio_url
                    },
                    "background": {
                        "value": "#1E1E2E",
                        "fit": "cover",
                        "type": "color"
                    }
                }
            ]
        }
        
        video_response = requests.post(
            "https://api.heygen.com/v2/video/generate",
            headers=headers,
            json=generate_payload
        )
        
        if video_response.status_code != 200:
            raise Exception(f"Failed to initiate video generation: {video_response.text}")
            
        video_id = video_response.json().get("data", {}).get("video_id")
        print(f"Video generation started. Video ID: {video_id}")
        
        # 3. Poll for completion
        print("Polling for video completion...")
        video_url = None
        while True:
            status_response = requests.get(
                f"https://api.heygen.com/v3/videos/{video_id}",
                headers=headers
            )
            
            if status_response.status_code != 200:
                raise Exception(f"Error fetching video status: {status_response.text}")
                
            status_data = status_response.json().get("data", {})
            status = status_data.get("status")
            
            print(f"Video status: {status}")
            if status == "completed":
                video_url = status_data.get("video_url")
                break
            elif status == "failed":
                error_msg = status_data.get("failure_message", "Unknown error")
                raise Exception(f"Video generation failed: {error_msg}")
                
            time.sleep(15)
            
        # 4. Download generated video and upload to S3
        print(f"Downloading completed video from {video_url}...")
        mp4_response = requests.get(video_url, stream=True)
        if mp4_response.status_code != 200:
            raise Exception("Failed to download generated video from HeyGen")
            
        local_mp4_path = f"/tmp/{video_id}.mp4"
        with open(local_mp4_path, 'wb') as f:
            for chunk in mp4_response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        output_s3_key = s3_key.rsplit('.', 1)[0] + f"_heygen_{video_id}.mp4"
        print(f"Uploading generated video to s3://{s3_bucket}/{output_s3_key}")
        
        s3_client.upload_file(
            local_mp4_path,
            s3_bucket,
            output_s3_key,
            ExtraArgs={'ContentType': 'video/mp4'}
        )
        
        if os.path.exists(local_mp3_path):
            os.remove(local_mp3_path)
        if os.path.exists(local_mp4_path):
            os.remove(local_mp4_path)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Avatar video generated and uploaded successfully',
                'video_id': video_id,
                's3_bucket': s3_bucket,
                's3_key': output_s3_key
            })
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps(f"Internal server error: {str(e)}")
        }
