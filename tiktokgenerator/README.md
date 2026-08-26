# TikTok Generator Lambda

This Lambda function is triggered by S3 when a new `.mp4` file is created. It uses the Buffer.com API to schedule a post for TikTok (or other social media channels).

## Configuration

The following environment variables must be set:

- `BUFFER_ACCESS_TOKEN`: Your Buffer API access token.
- `BUFFER_PROFILE_ID`: The ID(s) of the social media profiles to post to (comma-separated if multiple).
- `S3_BUCKET_NAME`: The name of the S3 bucket where the videos are stored.

## Deployment

1. Build and push the Docker image:
   ```bash
   ./.utils/build-and-upload.bash tiktokgenerator [LambdaFunctionName]
   ```

2. Configure S3 Event Notifications:
   - Go to your S3 bucket in the AWS Console.
   - Under **Properties** -> **Event notifications**, create a new event.
   - Event type: `All object create events` (or specifically `s3:ObjectCreated:*`).
   - Suffix: `.mp4`
   - Destination: Lambda function (select the `tiktokgenerator` function).

## How it works

1. It receives the S3 event containing the bucket and key of the new video.
2. It generates a pre-signed URL for the video (valid for 1 hour) so Buffer can download it.
3. It sends a POST request to Buffer's GraphQL API (`https://api.buffer.com`) to schedule the post using the `createPost` mutation and `VideoAssetInput`.
