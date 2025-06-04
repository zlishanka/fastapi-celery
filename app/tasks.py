# app/tasks.py

"""
celery -A tasks worker --loglevel=info -P gevent -c 100     # Greenlets
celery -A tasks worker --loglevel=info -P prefork -c 4      # Multiprocessing
celery -A tasks worker --loglevel=info -P --autoscale=100,5 # Dynamic
celery -A tasks.celery_app flower --port=5555 

"""

from celery import Celery, states
from typing import Dict, any
import asyncio
import nest_asyncio
import httpx  # Async HTTP client
from typing import Dict
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration

import boto3
from botocore.exceptions import ClientError

sentry_sdk.init(
    dsn="YOUR_SENTRY_DSN",
    integrations=[CeleryIntegration()]
)

# Apply patch to allow nested event loops
nest_asyncio.apply()

celery_app = Celery(
    "tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

# Set soft/hard timeouts so long-waiting tasks don't hang forever
# all exceptions in your Celery tasks will be sent to Sentry with full stack trace and context.
celery_app.conf.update(
    task_soft_time_limit=300,  # 5 minutes
    task_time_limit=600,       # 10 minutes
    task_default_queue='default',
    task_send_sent_event=True,
    worker_send_task_events='state_changed',
)

# Configure AWS SQS
AWS_REGION = 'us-west-1'
SQS_QUEUE_URL = 'https://sqs.us-east-1.amazonaws.com/your-account-id/gpu-processing-queue' 

sqs_client = boto3.client('sqs', region_name=AWS_REGION)


@celery_app.task(name="tasks.process_manifest_request")
def process_manifest_request(video_id: str) -> Dict[str, str]:
    """
    Simulate background processing of manifest request.
    In real life, this would fetch from DB or CDN.
    """
    print(f"Processing manifest request for {video_id}")
    return {
        "video_id": video_id,
        "playlist_url": f"https://cdn.example.com/streams/{video_id}/master.m3u8" 
    }

# Use Retries with Exponential Backoff
@celery_app.task(name="tasks.process_video_async", bind=True, retry_kwargs={'max_retries': 3})
async def process_video_async(self, video_id: str) -> Dict[str, str]:
    """
    Sends a video for video processing via external API and waits for response.
    """
    url = "https://video-processing.example.com/api/process" 

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json={"video_id": video_id}, timeout=300)
            response.raise_for_status()
            return {"video_id": video_id, "status": "processed", "result_url": response.json()["url"]}
        except httpx.HTTPError as exc:
            raise self.retry(exc=exc)

@celery_app.task(name="tasks.process_gpu_async", bind=True, acks_late=True)
async def process_gpu_async(self, video_id: str) -> Dict[str, str]:
    """
    Celery task that submits a GPU processing job via AWS SQS.
    Does NOT wait for result — returns immediately after sending message.
    """
    try:
        # Send message to SQS queue
        response = sqs_client.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps({
                "task_id": self.request.id,
                "video_id": video_id,
                "callback_url": "http://your-api.com/callback/gpu"
            }),
            MessageGroupId="gpu_jobs"  # Required if using FIFO queues
        )

        return {
            "status": "queued",
            "message_id": response.get("MessageId"),
            "video_id": video_id,
            "task_id": self.request.id
        }

    except ClientError as e:
        raise self.retry(exc=e, countdown=10, max_retries=3)
