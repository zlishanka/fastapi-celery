from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import LiveVideo
from app.schemas import LiveVideoCreate


async def get_video_by_id(db: AsyncSession, video_id: int):
    result = await db.execute(select(LiveVideo).where(LiveVideo.id == video_id))
    return result.scalars().first()


async def create_video(db: AsyncSession, video: LiveVideoCreate):
    db_video = LiveVideo(**video.model_dump())
    db.add(db_video)
    await db.commit()
    await db.refresh(db_video)
    return db_video