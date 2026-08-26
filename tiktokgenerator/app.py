import json
import os
import boto3
import requests
from urllib.parse import unquote_plus

def handler(event, context):
    print("Received event: " + json.dumps(event, indent=2))
    
    buffer_access_token = os.environ.get("BUFFER_ACCESS_TOKEN")
    buffer_profile_id = os.environ.get("BUFFER_PROFILE_ID")
    podcast_title = os.environ.get("PODCAST_TITLE", "Tech & IA Horizon")
    cloudfront_domain = os.environ.get("CLOUDFRONT_DOMAIN")
    
    if not buffer_access_token or not buffer_profile_id:
        print("BUFFER_ACCESS_TOKEN or BUFFER_PROFILE_ID not set")
        return {'statusCode': 400, 'body': 'Buffer credentials not set'}
        
    s3_client = boto3.client('s3')
    
    try:
        for record in event.get('Records', []):
            bucket = record['s3']['bucket']['name']
            key = unquote_plus(record['s3']['object']['key'], encoding='utf-8')
            
            if not key.endswith('.mp4'):
                print(f"Skipping non-mp4 file: {key}")
                continue
                
            print(f"Processing video: {key} from bucket: {bucket}")
            
            if cloudfront_domain:
                video_url = f"https://{cloudfront_domain}/{key}"
            else:
                video_url = s3_client.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': bucket, 'Key': key},
                    ExpiresIn=3600
                )
            
            endpoint = "https://api.buffer.com/graphql"
            channel_ids = [pid.strip() for pid in buffer_profile_id.split(',')]
            
            headers = {
                'Authorization': f'Bearer {buffer_access_token}',
                'Content-Type': 'application/json'
            }

            post_text_template = os.environ.get(
                "POST_TEXT_TEMPLATE",
                f"{podcast_title} en 1min #tech #ia #podcast #shorts"
            )
            post_text = post_text_template

            for channel_id in channel_ids:
                print(f"Creating Buffer post for channel: {channel_id}")
                
                mutation = f"""
                mutation CreatePost {{
                  createPost(input: {{
                    text: "{post_text}",
                    channelId: "{channel_id}",
                    schedulingType: automatic,
                    mode: addToQueue,
                    assets: {{
                      videos: [
                        {{
                          url: "{video_url}"
                        }}
                      ]
                    }}
                  }}) {{
                    ... on PostActionSuccess {{
                      post {{
                        id
                        text
                        assets {{
                          source
                        }}
                      }}
                    }}
                    ... on MutationError {{
                      message
                    }}
                  }}
                }}
                """

                response = requests.post(
                    endpoint,
                    headers=headers,
                    json={"query": mutation}
                )
                
                print(f"Buffer response status for {channel_id}: {response.status_code}")
                print(f"Buffer response for {channel_id}: {response.text}")
            
        return {'statusCode': 200, 'body': 'Buffer post creation triggered.'}
    except Exception as e:
        print(f"Error: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
