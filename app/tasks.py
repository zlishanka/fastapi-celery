# app/tasks.py
from celery import Celery
from typing import Dict

celery_app = Celery(
    "tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)


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