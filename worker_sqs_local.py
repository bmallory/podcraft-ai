#!/usr/bin/env python3
"""
Worker Local SQS & Step Functions Task Token.
Permet d'exécuter des traitements IA locaux lourds (sur machine GPU locale)
en étant orchestré de manière asynchrone par AWS Step Functions.

Fonctionnement :
1. Écoute les jobs sur la file SQS via long-polling.
2. Télécharge l'audio, le script et l'image depuis S3.
3. Exécute votre pipeline IA local (ComfyUI / Diffusers / FFmpeg).
4. Téléverse la vidéo finale vers S3.
5. Renvoie le signal send_task_success avec le TaskToken à Step Functions.
"""

import os
import sys
import time
import json
import signal
import logging
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("LocalSQSWorker")

# Configuration AWS
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
QUEUE_URL = os.environ.get("SQS_QUEUE_URL")
OUTPUT_LOCAL_DIR = Path(os.environ.get("WORKER_OUTPUT_DIR", "./worker_workspace"))
OUTPUT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)

# Initialisation des clients AWS
sqs_client = boto3.client("sqs", region_name=AWS_REGION)
s3_client = boto3.client("s3", region_name=AWS_REGION)
sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)

running = True


def handle_shutdown(signum, frame):
    global running
    logger.info("Signal d'arrêt reçu (%s). Arrêt gracieux du worker...", signum)
    running = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def download_s3_file(bucket: str, key: str, local_destination: Path) -> Path:
    local_destination.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Téléchargement de s3://%s/%s vers %s", bucket, key, local_destination)
    s3_client.download_file(bucket, key, str(local_destination))
    return local_destination


def upload_s3_file(local_path: Path, bucket: str, key: str, content_type: str = "video/mp4"):
    logger.info("Téléversement de %s vers s3://%s/%s", local_path, bucket, key)
    with open(local_path, "rb") as f:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=f,
            ContentType=content_type
        )


def execute_local_ia_inference(
    audio_path: Path,
    script_path: Path = None,
    image_path: Path = None,
    timestamp: str = None
) -> Path:
    """
    Emplacement du traitement IA lourd sur votre machine locale (GPU).
    Exemples :
    - Appel à ComfyUI / Automatic1111 / InvokeAI via API REST locale
    - Inférence locale PyTorch / Diffusers (Wan2.1, HunyuanVideo, SadTalker)
    - Montage vidéo FFmpeg avec sous-titres animés et audio
    """
    logger.info(">>> Début du traitement local IA...")
    output_filename = f"short_final_{timestamp or int(time.time())}.mp4"
    output_path = OUTPUT_LOCAL_DIR / output_filename

    # =========================================================================
    # Personnalisez ici votre code d'inférence réel !
    # Exemple :
    #   subprocess.run(["python3", "run_inference.py", "--audio", str(audio_path), ...])
    # =========================================================================
    logger.info("Traitement en cours avec Audio: %s, Image: %s", audio_path, image_path)
    time.sleep(3)  # Simulation d'un traitement

    if not output_path.exists():
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"Generated video placeholder for timestamp {timestamp}")

    logger.info("<<< Traitement local terminé : %s", output_path)
    return output_path


def process_message(msg):
    receipt_handle = msg["ReceiptHandle"]
    body_str = msg["Body"]

    try:
        body = json.loads(body_str)
    except json.JSONDecodeError as err:
        logger.error("Message non JSON ignoré : %s (%s)", body_str, err)
        sqs_client.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)
        return

    task_token = body.get("taskToken")
    if not task_token:
        logger.warning("Message sans taskToken, suppression du message.")
        sqs_client.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)
        return

    bucket = body.get("s3Bucket") or body.get("s3_bucket")
    audio_key = body.get("s3AudioKey") or body.get("s3_audio_key") or body.get("s3Key") or body.get("s3_key")
    script_key = body.get("s3ScriptKey") or body.get("s3_script_key")
    image_key = body.get("s3ImageKey") or body.get("s3_image_key")
    timestamp = body.get("timestamp") or time.strftime("%Y%m%d_%H%M%S")

    logger.info("--------------------------------------------------")
    logger.info("Job reçu ! Bucket: %s, AudioKey: %s", bucket, audio_key)

    local_audio_path = None
    local_script_path = None
    local_image_path = None

    try:
        job_dir = OUTPUT_LOCAL_DIR / timestamp
        job_dir.mkdir(parents=True, exist_ok=True)

        if bucket and audio_key:
            local_audio_path = download_s3_file(bucket, audio_key, job_dir / Path(audio_key).name)
        if bucket and script_key:
            local_script_path = download_s3_file(bucket, script_key, job_dir / Path(script_key).name)
        if bucket and image_key:
            local_image_path = download_s3_file(bucket, image_key, job_dir / Path(image_key).name)

        # Exécution du traitement local
        output_video_path = execute_local_ia_inference(
            audio_path=local_audio_path,
            script_path=local_script_path,
            image_path=local_image_path,
            timestamp=timestamp
        )

        # Téléversement du résultat vers S3
        output_s3_key = f"shorts/videos/{output_video_path.name}"
        upload_s3_file(output_video_path, bucket, output_s3_key)

        # Notification de succès à Step Functions via le TaskToken
        logger.info("Envoi du callback Step Functions send_task_success...")
        success_output = json.dumps({
            "status": "SUCCESS",
            "outputS3Bucket": bucket,
            "outputS3Key": output_s3_key,
            "timestamp": timestamp,
            "workerMessage": "Traitement local IA exécuté avec succès."
        })
        sfn_client.send_task_success(
            taskToken=task_token,
            output=success_output
        )
        logger.info("Callback send_task_success envoyé avec succès !")

        # Suppression du message SQS
        sqs_client.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)
        logger.info("Message SQS supprimé. Job terminé.")

    except Exception as exc:
        logger.exception("Erreur lors de l'exécution locale du job : %s", exc)
        try:
            logger.info("Notification d'échec à Step Functions send_task_failure...")
            sfn_client.send_task_failure(
                taskToken=task_token,
                error=type(exc).__name__,
                cause=str(exc)
            )
        except Exception as sfn_err:
            logger.error("Impossible d'envoyer send_task_failure : %s", sfn_err)

        # Supprime le message de la file
        sqs_client.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)


def main():
    if not QUEUE_URL:
        logger.error("Erreur : La variable d'environnement SQS_QUEUE_URL n'est pas définie.")
        logger.info("Veuillez l'exporter : export SQS_QUEUE_URL=\"https://sqs.<region>.amazonaws.com/<account>/<queue>\"")
        sys.exit(1)

    logger.info("Démarrage du worker SQS local...")
    logger.info("Région : %s", AWS_REGION)
    logger.info("File SQS : %s", QUEUE_URL)
    logger.info("En attente de messages (long-polling)... Appuyez sur Ctrl+C pour arrêter.")

    while running:
        try:
            response = sqs_client.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                AttributeNames=["All"]
            )
            messages = response.get("Messages", [])
            if not messages:
                continue

            for msg in messages:
                process_message(msg)

        except ClientError as client_err:
            logger.error("Erreur client AWS SQS : %s", client_err)
            time.sleep(5)
        except Exception as e:
            logger.error("Erreur inattendue dans la boucle principale : %s", e)
            time.sleep(5)

    logger.info("Worker local arrêté proprement.")


if __name__ == "__main__":
    main()
