# Docker Lambda Python (Thematic Podcast & Shorts Generator)

This project provides a multi-component Docker-based AWS Lambda environment that scrapes specialized thematic sources, uses Google's Gemini API to synthesize daily thematic podcast scripts, generates high-quality audio, mixes background music, and automatically produces "Shorts" and videos for social media.

## Project Structure

- **`Main/`**: The core thematic podcast generator.
  - Scrapes news/articles from configurable thematic sources (`config.json`) with anti-bot detection and RSS fallbacks.
  - Synthesizes a structured podcast script and rich RSS HTML description (with sources) using `gemini-3.7-flash`.
  - Uploads the script and sources description files to S3.
  - Generates a multi-modal audio file (MP3) using Gemini TTS (`gemini-3.1-flash-tts-preview`) and FFmpeg.
  - Contains `test_scraper.py` for diagnostic testing of news scrapers without incurring Gemini costs.
- **`AudioMixer/`**: Background music mixer.
  - Dynamically mixes background music from `ressources/music/` in S3 with the generated voice track using FFmpeg (`amix`, `afade`).
  - Overwrites the original MP3 in S3 with the mixed version (gracefully falls back if no music tracks are present).
- **`ShortsGenerator/`**: Social media summary, audio, and illustration generator.
  - Generates short high-impact summary scripts (~45s).
  - Synthesizes short audio via Gemini TTS and FFmpeg to MP3.
  - Generates photorealistic AI illustrations via Imagen 3 / Gemini Image.
- **`SpotifyUploader/`**: Automated Spotify publisher using `save-to-spotify` CLI.
- **`VideoGenerator/`**: Cloud video generation module powered by Google Veo & FFmpeg.
- **`worker_sqs_local.py`**: Local GPU worker for heavy AI video/animation processing via Step Functions Task Token over SQS.
- **`.utils/`**:
  - `config.env`: Central environment and resource naming configuration.
  - `setup-aws-infra.bash`: Bootstraps dedicated S3 bucket and IAM roles.
  - `build-and-upload.bash`: Builds, tags, and pushes ECR container images and updates Lambdas.
  - `deploy-audiomixer.bash`: Deploys AudioMixer Lambda function.
  - `deploy-stepfunction.bash`: Configures SQS queue and IAM permissions for the Step Function and local worker.
  - `deploy-videogenerator.bash`: Deploys VideoGenerator Lambdas & Step Function.
  - `deploy-pipeline.bash`: Deploys Master Step Function workflow.
  - `deploy-all.bash`: End-to-end deployment script.
  - `test-scraping.bash`: Runs scraping diagnostic tests inside Docker container.
  - `run-local.bash`: Local execution script.

## AWS Resource Isolation

All AWS resources (S3, ECR, Lambda functions, Step Functions, IAM roles, Secrets Manager) are prefixed with `PROJECT_PREFIX` (default: `thematic-podcast`), ensuring zero conflict when running alongside `docker-lambda` on the same AWS account.
