# app/main.py
"""
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
# ~250 MB per worker 
"""
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_cache.decorator import cache
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_cache import FastAPICache
from fastapi_cache.decorator import cache

from fastapi import WebSocket, APIRouter, BackgroundTasks
from celery import Celery
from celery.result import AsyncResult
import json
import asyncio

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse

from app import models, schemas, auth, tasks, crud
from app.database import get_db, engine, Base
from sqlalchemy.future import select
from app.schemas import GPUCallbackRequest
from app.cache import init_redis_cache

router = APIRouter() 

# Use same Celery config from tasks.py
celery_app = Celery(
    'tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0'
)

# Add Callback Route for gpu related tasks
@router.post("/callback/gpu")
async def handle_gpu_callback(payload: GPUCallbackRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(update_celery_task_state, payload.task_id, payload.status, payload.result_url)
    return {"status": "received"}

def update_celery_task_state(task_id: str, status: str, result_url: str):
    """
    Manually update Celery task state in Redis.
    """
    if status == "success":
        celery_app.backend.mark_as_success(task_id, result={
            "video_id": result_url.split("/")[-1],
            "result_url": result_url
        })
    elif status == "failed":
        celery_app.backend.mark_as_failure(task_id, Exception("GPU processing failed"))

# Create WebSocket Endpoint
# Updates When Task Completes
@router.websocket("/ws/task/{task_id}")
async def websocket_task_update(websocket: WebSocket, task_id: str):
    await websocket.accept()
    
    # Polling loop
    while True:
        task_result = celery_app.AsyncResult(task_id)
        await websocket.send_text(json.dumps({
            "task_id": task_id,
            "status": task_result.status,
            "result": task_result.result
        }))
        
        if task_result.state in ["SUCCESS", "FAILURE"]:
            break
        
        await asyncio.sleep(1)  # poll every second

    await websocket.close()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Streaming Service")
app.state.limiter = limiter

# Rate limit error handler
@app.exception_handler(RateLimitExceeded)
def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "Too many requests", "retry_after": exc.period_remaining},
    )


# Create DB tables
@app.on_event("startup")
async def startup_event():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_redis_cache()


# --- Auth Routes ---
@app.post("/token", response_model=schemas.Token)
@limiter.limit("5/minute")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await auth.authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}


# --- Manifest Route ---
@app.get("/manifest")
@limiter.limit("60/minute")
def get_manifest(video_id: str):
    """
    internal DB query, quick check
    """
    task = tasks.process_manifest_request(video_id)
    return {"task_id": task.id}


# --- Video Processing Route ---
@app.post("/video_processing")
@limiter.limit("10/minute")
async def start_video_processing(
    request: schemas.VideoProcessingRequest
):
    """
    Trigger async video processing via external service.
    Returns task_id for checking status later.
    """
    result = await tasks.process_video_async(video_id=request.video_id)
    return result

# --- GPU Processing Route ---
@app.post("/gpu_processing")
@limiter.limit("5/minute")
async def start_gpu_processing(
    request: schemas.GpuProcessingRequest
):
    """
    Trigger async GPU processing via external service.
    Returns task_id for checking status later.
    """
    task = tasks.process_gpu_async.delay(video_id=request.video_id)
    return {"task_id": task.id} 

# --- Task Status Route ---
@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    task_result = tasks.celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": task_result.status,
        "result": task_result.result
    }


# --- Video Management Routes ---
@app.post("/videos", response_model=schemas.LiveVideoOut)
async def create_video(video: schemas.LiveVideoCreate, db: AsyncSession = Depends(get_db)):
    return await crud.create_video(db, video)


@app.get("/videos", response_model=list[schemas.LiveVideoOut])
@cache(expire=60)  # cache for 60 seconds
async def list_videos(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo))
    return result.scalars().all()


@app.get("/videos/{video_id}", response_model=schemas.LiveVideoOut)
@cache(expire=60)  # cache for 60 seconds
async def get_video(video_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo).where(models.LiveVideo.id == video_id))
    video = result.scalars().first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@app.put("/videos/{video_id}", response_model=schemas.LiveVideoOut)
async def update_video(video_id: int, updated: schemas.LiveVideoCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo).where(models.LiveVideo.id == video_id))
    video = result.scalars().first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    for key, val in updated.model_dump().items():
        setattr(video, key, val)
    await db.commit()
    await db.refresh(video)
    await FastAPICache.clear(namespace="get_popular_videos")  # clear specific key
    await FastAPICache.clear()  # clear all cache
    return video


@app.delete("/videos/{video_id}", status_code=204)
async def delete_video(video_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo).where(models.LiveVideo.id == video_id))
    video = result.scalars().first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    await db.delete(video)
    await db.commit()
    return

app.include_router(router)