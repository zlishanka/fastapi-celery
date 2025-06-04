# app/main.py
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse

from app import models, schemas, auth, tasks, crud
from app.database import get_db, engine, Base
from sqlalchemy.future import select

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
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
@limiter.limit("10/minute")
async def get_manifest(video_id: str):
    task = tasks.process_manifest_request.delay(video_id)
    return {"task_id": task.id}


# --- Video Processing Route ---
@app.post("/video_processing")
@limiter.limit("20/minute")
async def start_video_processing(
    request: schemas.VideoProcessingRequest
):
    """
    Trigger async video processing via external service.
    Returns task_id for checking status later.
    """
    task = tasks.process_video_async.delay(video_id=request.video_id)
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


# --- Admin Routes ---
@app.post("/admin/videos", response_model=schemas.LiveVideoOut)
async def create_video(video: schemas.LiveVideoCreate, db: AsyncSession = Depends(get_db)):
    return await crud.create_video(db, video)


@app.get("/admin/videos", response_model=list[schemas.LiveVideoOut])
async def list_videos(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo))
    return result.scalars().all()


@app.get("/admin/videos/{video_id}", response_model=schemas.LiveVideoOut)
async def get_video(video_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo).where(models.LiveVideo.id == video_id))
    video = result.scalars().first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@app.put("/admin/videos/{video_id}", response_model=schemas.LiveVideoOut)
async def update_video(video_id: int, updated: schemas.LiveVideoCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo).where(models.LiveVideo.id == video_id))
    video = result.scalars().first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    for key, val in updated.model_dump().items():
        setattr(video, key, val)
    await db.commit()
    await db.refresh(video)
    return video


@app.delete("/admin/videos/{video_id}", status_code=204)
async def delete_video(video_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.LiveVideo).where(models.LiveVideo.id == video_id))
    video = result.scalars().first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    await db.delete(video)
    await db.commit()
    return