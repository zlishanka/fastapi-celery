# app/schemas.py
from pydantic import BaseModel
from typing import Optional


class UserCreate(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str


class Token(BaseModel):
    access_token: str
    token_type: str


class LiveVideoCreate(BaseModel):
    video_id: str
    stream_key: str
    master_playlist: str


class LiveVideoOut(LiveVideoCreate):
    id: int

class VideoProcessingRequest(BaseModel):
    video_id: str

class GpuProcessingRequest(BaseModel):
    video_id: str

class GPUCallbackRequest(BaseModel):
    task_id: str
    video_id: str
    result_url: str
    status: str  # "success" or "failed"