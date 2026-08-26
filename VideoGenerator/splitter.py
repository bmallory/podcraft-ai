import json
import os
import urllib.parse
import boto3
import textwrap
from datetime import datetime

def handler(event, context):
    print("Received event: " + json.dumps(event, indent=2))
    
    s3_client = boto3.client('s3')
    
    bucket = None
    key = None
    
    # Check for direct S3 event
    if 'Records' in event:
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'], encoding='utf-8')
    # Check for EventBridge S3 event
    elif 'detail' in event and 'bucket' in event['detail']:
        bucket = event['detail']['bucket']['name']
        key = urllib.parse.unquote_plus(event['detail']['object']['key'], encoding='utf-8')
    else:
        # Check if it's already unwrapped or custom event
        bucket = event.get('bucket')
        key = event.get('key')
        
    if not bucket or not key:
        raise Exception("Could not find S3 bucket and key in event")

    if not key.endswith('.md') or not os.path.basename(key).startswith('short_'):
        print(f"Skipping file: {key}")
        # Return empty payload to stop Step Function execution gracefully
        return {'skip': True, 'reason': 'Not a short_*.md file'}
        
    print(f"Processing script: {key} from bucket: {bucket}")
    
    # Download the script
    response = s3_client.get_object(Bucket=bucket, Key=key)
    script_text = response['Body'].read().decode('utf-8')
    
    text_chunks = textwrap.wrap(script_text, width=75, break_long_words=False)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.basename(key).replace('short_', '').replace('.md', '')
    
    chunks_payload = []
    for i, text in enumerate(text_chunks):
        chunks_payload.append({
            'index': i,
            'text': text,
            'bucket': bucket,
            'timestamp': timestamp,
            'base_name': base_name,
            'total_chunks': len(text_chunks)
        })
        
    result = {
        'skip': False,
        'bucket': bucket,
        'timestamp': timestamp,
        'base_name': base_name,
        'total_chunks': len(text_chunks),
        'chunks': chunks_payload
    }
    
    print(f"Splitter result: {json.dumps(result)}")
    return result
