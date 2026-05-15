"""
database.py — SQLite database setup using SQLAlchemy
Tables: users, saved_voices
"""

import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DB_PATH = os.path.join(os.path.dirname(__file__), "langwarp.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # needed for SQLite + FastAPI
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Models ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    email         = Column(String, unique=True, index=True, nullable=False)
    username      = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)
    is_active     = Column(Boolean, default=True)

    voices = relationship("SavedVoice", back_populates="user", cascade="all, delete-orphan")


class SavedVoice(Base):
    __tablename__ = "saved_voices"

    id                  = Column(Integer, primary_key=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False)
    elevenlabs_voice_id = Column(String, nullable=False)
    voice_name          = Column(String, nullable=False)   # e.g. "John's Voice"
    language            = Column(String, default="en")     # primary language of speaker
    created_at          = Column(DateTime, default=datetime.utcnow)
    last_used_at        = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="voices")


# ── Init ───────────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    print(f"[db] Database ready at {DB_PATH}")


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()