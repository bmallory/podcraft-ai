import json
import os
import boto3
import subprocess
import time
from google import genai
from google.genai import types

def handler(event, context):
    print("Received chunk event: " + json.dumps(event, indent=2))
    
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        raise Exception('GEMINI_API_KEY not set')
        
    client = genai.Client(api_key=gemini_api_key)
    s3_client = boto3.client('s3')
    
    index = event['index']
    text = event['text']
    bucket = event['bucket']
    timestamp = event['timestamp']
    
    video_model = os.environ.get("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")
    presenter_prompt = os.environ.get(
        "VIDEO_PRESENTER_PROMPT",
        "un animateur de podcast tech jeune et charismatique dans un studio moderne qui s'exprime face caméra de manière captivante"
    )
    
    print(f"Generating segment {index+1} with {video_model}...")
    
    config_args = {
        "aspect_ratio": "9:16"
    }
    
    if index > 0:
        prev_index = index - 1
        prev_segment_key = f"videos/chunks/{timestamp}/segment_{prev_index}.mp4"
        prev_segment_path = f"/tmp/segment_{timestamp}_{prev_index}.mp4"
        
        print(f"Downloading previous segment {prev_segment_key} from {bucket}")
        s3_client.download_file(bucket, prev_segment_key, prev_segment_path)
        
        frame_file = f"/tmp/frame_{timestamp}_{index}.jpg"
        
        # Extract last frame using ffmpeg
        print(f"Extracting last frame from {prev_segment_path}")
        process = subprocess.Popen(
            [
                '/opt/bin/ffmpeg', '-y', 
                '-i', prev_segment_path,
                '-update', '1', '-q:v', '2',
                frame_file
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise Exception(f"FFmpeg frame extraction failed: {stderr.decode('utf-8')}")
        
        # Read bytes
        with open(frame_file, "rb") as f:
            frame_bytes = f.read()
            
        config_args["reference_images"] = [
            types.VideoGenerationReferenceImage(
                image=types.Image(
                    image_bytes=frame_bytes,
                    mime_type="image/jpeg"
                ),
                reference_type="asset"
            )
        ]

    config = types.GenerateVideosConfig(**config_args)

    # Generate video segment
    prompt = f"{presenter_prompt}. Règle impérative : le texte doit être prononcé mot pour mot, sans en rajouter ou reformuler. Texte à lire : {text}"
    operation = client.models.generate_videos(
        model=video_model,
        prompt=prompt,
        config=config
    )

    while not operation.done:
        print("Waiting for video generation to complete...")
        time.sleep(10)
        operation = client.operations.get(operation)
        
    if not operation.response or not operation.response.generated_videos:
        raise Exception(f"Video generation failed or empty response. Error: {getattr(operation, 'error', 'Unknown')}")
        
    segment_path = f"/tmp/segment_{timestamp}_{index}.mp4"
    video = operation.response.generated_videos[0]
    
    # Download generated video
    client.files.download(file=video.video)
    video.video.save(segment_path)
    
    # Upload to S3
    segment_key = f"videos/chunks/{timestamp}/segment_{index}.mp4"
    print(f"Uploading {segment_key} to {bucket}")
    with open(segment_path, "rb") as f_mp4:
        s3_client.put_object(
            Bucket=bucket,
            Key=segment_key,
            Body=f_mp4,
            ContentType='video/mp4'
        )
        
    # Cleanup
    if os.path.exists(segment_path):
        os.remove(segment_path)
    if index > 0:
        if os.path.exists(prev_segment_path):
            os.remove(prev_segment_path)
        frame_file = f"/tmp/frame_{timestamp}_{index}.jpg"
        if os.path.exists(frame_file):
            os.remove(frame_file)

    return {
        "status": "success",
        "segment_key": segment_key,
        "index": index
    }
