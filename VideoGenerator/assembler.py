import json
import os
import boto3
import subprocess
from datetime import datetime

def handler(event, context):
    print("Received event: " + json.dumps(event, indent=2))
    
    s3_client = boto3.client('s3')
    
    bucket = event['bucket']
    timestamp = event['timestamp']
    base_name = event['base_name']
    total_chunks = event['total_chunks']
    
    video_segments = []
    
    # Download all chunks
    for i in range(total_chunks):
        segment_key = f"videos/chunks/{timestamp}/segment_{i}.mp4"
        segment_path = f"/tmp/segment_{timestamp}_{i}.mp4"
        
        print(f"Downloading {segment_key} from {bucket}")
        s3_client.download_file(bucket, segment_key, segment_path)
        video_segments.append(segment_path)
        
    # Concatenate all segments
    print("Concatenating segments...")
    concat_list_path = f"/tmp/concat_list_{timestamp}.txt"
    with open(concat_list_path, "w") as f:
        for seg in video_segments:
            f.write(f"file '{seg}'\n")
            
    final_video_path = f"/tmp/final_video_{timestamp}.mp4"
    process = subprocess.Popen(
        [
            '/opt/bin/ffmpeg', '-y', 
            '-f', 'concat', '-safe', '0', 
            '-i', concat_list_path,
            '-c', 'copy', final_video_path
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise Exception(f"FFmpeg concatenation failed: {stderr.decode('utf-8')}")
        
    # Upload final video
    date_of_the_day = datetime.now().strftime("%Y-%m-%d")
    final_key = f"videos/{date_of_the_day}_video_{base_name}.mp4"
    
    print(f"Uploading {final_key} to {bucket}")
    with open(final_video_path, "rb") as f_mp4:
        s3_client.put_object(
            Bucket=bucket,
            Key=final_key,
            Body=f_mp4,
            ContentType='video/mp4'
        )
        
    # Cleanup S3 chunks
    print("Cleaning up S3 chunks...")
    for i in range(total_chunks):
        segment_key = f"videos/chunks/{timestamp}/segment_{i}.mp4"
        try:
            s3_client.delete_object(Bucket=bucket, Key=segment_key)
        except Exception as e:
            print(f"Failed to delete {segment_key}: {e}")
            
    # Cleanup local tmp
    for seg in video_segments:
        if os.path.exists(seg): os.remove(seg)
    if os.path.exists(concat_list_path): os.remove(concat_list_path)
    if os.path.exists(final_video_path): os.remove(final_video_path)
    
    return {
        'statusCode': 200,
        'body': 'Video successfully assembled.',
        'final_video_key': final_key
    }
