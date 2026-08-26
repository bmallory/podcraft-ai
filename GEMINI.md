# Docker Lambda Python (Thematic Podcast & Shorts Generator)

This project provides a multi-component Docker-based AWS Lambda environment that scrapes specialized thematic sources, uses Google's Gemini API to synthesize daily thematic podcast scripts, generates high-quality audio, and automatically produces "Shorts" and videos for social media.

## Project Structure

- **`Main/`**: The core thematic podcast generator.
  - Scrapes news/articles from configurable thematic sources (`config.json`).
  - Synthesizes a structured podcast script in French using `gemini-3.7-flash`.
  - Uploads the script as a Markdown file to S3.
  - Generates a multi-modal audio file (MP3) using Gemini TTS (`gemini-3.1-flash-tts-preview`) and FFmpeg.
- **`ShortsGenerator/`**: Social media summary and illustration generator.
  - Generates 1-minute high-impact summary scripts.
  - Generates photorealistic AI illustrations via Imagen / Gemini Image.
- **`SpotifyUploader/`**: Automated Spotify publisher using `save-to-spotify` CLI.
- **`VideoGenerator/`**: Video generation module powered by Google Veo & FFmpeg.
- **`.utils/`**:
  - `config.env`: Central environment and resource naming configuration.
  - `setup-aws-infra.bash`: Bootstraps dedicated S3 bucket and IAM roles.
  - `build-and-upload.bash`: Builds, tags, and pushes ECR container images and updates Lambdas.
  - `deploy-videogenerator.bash`: Deploys VideoGenerator Lambdas & Step Function.
  - `deploy-pipeline.bash`: Deploys Master Step Function workflow.
  - `deploy-all.bash`: End-to-end deployment script.
  - `run-local.bash`: Local execution script.

## AWS Resource Isolation

All AWS resources (S3, ECR, Lambda functions, Step Functions, IAM roles, Secrets Manager) are prefixed with `PROJECT_PREFIX` (default: `thematic-podcast`), ensuring zero conflict when running alongside `docker-lambda` on the same AWS account.
