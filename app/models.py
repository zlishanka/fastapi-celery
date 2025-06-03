# app/models.py
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

# ORM: Object Relational Mapping
# Maps python classes to database tables
# SQLAlchemy is a Python SQL toolkit and Object Relational Mapper that provides a full suite of
# tools needed to work with relational databases.
# ORM allows us to interact with the database using Python objects, making it easier to work with the data.

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)


class LiveVideo(Base):
    __tablename__ = "live_videos"
    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(String, unique=True, index=True)
    stream_key = Column(String)
    master_playlist = Column(String)  # e.g., "path/to/master.m3u8"