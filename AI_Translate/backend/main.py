"""
LangWarp — FastAPI Backend (v3 — Auth + Saved Voices)
Pipeline: Audio → Whisper (STT) → GPT-4o-mini (Translate) → ElevenLabs cloned voice (TTS)

New in v3:
  - User accounts (signup / login)
  - JWT authentication
  - SQLite database stores saved voice IDs per user
  - On login, saved voice is restored — no re-calibration needed
"""

import os
import json
import base64
import asyncio
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
import httpx

from database import init_db, get_db, User, SavedVoice
from auth import (
    hash_password, verify_password,
    create_token, get_current_user, get_optional_user
)

app = FastAPI(title="LangWarp API", version="3.0.0")

@app.on_event("startup")
async def startup():
    init_db()
    oai = os.environ.get("OPENAI_API_KEY", "")
    el  = os.environ.get("ELEVENLABS_API_KEY", "")
    print("[startup] OPENAI_API_KEY     :", "SET (" + oai[:8] + "...)" if oai else "MISSING ✗")
    print("[startup] ELEVENLABS_API_KEY :", "SET (" + el[:8]  + "...)" if el  else "MISSING ✗")

@app.on_event("shutdown")
async def shutdown_cleanup():
    """Delete session-only clones (not saved ones) from ElevenLabs on shutdown."""
    el = os.environ.get("ELEVENLABS_API_KEY", "")
    if not el:
        return
    print("[shutdown] Cleaning up session-only clones...")
    async with httpx.AsyncClient(timeout=10) as client:
        for key, state in session_voice_state.items():
            voice_id = state.get("clone_id")
            if voice_id and not state.get("is_saved"):
                try:
                    await client.delete(
                        f"https://api.elevenlabs.io/v1/voices/{voice_id}",
                        headers={"xi-api-key": el},
                    )
                    print(f"[shutdown] Deleted session clone {key} ({voice_id})")
                except Exception as e:
                    print(f"[shutdown] Delete warning: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ─────────────────────────────────────────────────────────────────────
SUPPORTED_LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French",
    "de": "German",  "it": "Italian", "pt": "Portuguese",
    "zh": "Chinese (Mandarin)", "ja": "Japanese", "ko": "Korean",
    "ar": "Arabic",  "hi": "Hindi",   "ru": "Russian",
}

OPENAI_FALLBACK_VOICES = {"speaker_a": "alloy", "speaker_b": "nova"}

# ── Session voice state ────────────────────────────────────────────────────────
# Keyed by "{user_id}:{speaker}" e.g. "42:speaker_a"
# For guests (not logged in) keyed by "guest:speaker_a" etc.
session_voice_state: dict = {}

def _session_key(user_id: Optional[int], speaker: str) -> str:
    prefix = str(user_id) if user_id else "guest"
    return f"{prefix}:{speaker}"

def _get_or_create_state(user_id: Optional[int], speaker: str) -> dict:
    key = _session_key(user_id, speaker)
    if key not in session_voice_state:
        session_voice_state[key] = {
            "samples": [], "total_seconds": 0.0,
            "clone_id": None, "clone_status": "none",
            "mime_type": "audio/webm", "is_saved": False,
        }
    return session_voice_state[key]


# ── Key helpers ────────────────────────────────────────────────────────────────
def _openai_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise HTTPException(500, "OPENAI_API_KEY not set")
    return key

def _eleven_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise HTTPException(500, "ELEVENLABS_API_KEY not set")
    return key

def _estimate_duration(audio_bytes: bytes) -> float:
    return len(audio_bytes) / 4000.0


# ══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

class SignupRequest(BaseModel):
    email: str
    username: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class SaveVoiceRequest(BaseModel):
    speaker: str        # "speaker_a" or "speaker_b"
    voice_name: str     # e.g. "John's Voice"
    language: str = "en"


@app.post("/api/auth/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    """Create a new user account."""
    # Check email and username aren't taken
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(400, "Email already registered")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(400, "Username already taken")
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    user = User(
        email=req.email,
        username=req.username,
        password_hash=hash_password(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token(user.id, user.username)
    return {
        "token": token,
        "user": {"id": user.id, "email": user.email, "username": user.username},
        "message": "Account created successfully",
    }


@app.post("/api/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """Log in and receive a JWT token."""
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")

    token = create_token(user.id, user.username)

    # Load their saved voices
    saved_voices = db.query(SavedVoice).filter(SavedVoice.user_id == user.id).all()
    voices_data = [
        {
            "id": v.id,
            "voice_name": v.voice_name,
            "elevenlabs_voice_id": v.elevenlabs_voice_id,
            "language": v.language,
            "created_at": v.created_at.isoformat(),
        }
        for v in saved_voices
    ]

    return {
        "token": token,
        "user": {"id": user.id, "email": user.email, "username": user.username},
        "saved_voices": voices_data,
    }


@app.get("/api/auth/me")
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get current user info + their saved voices."""
    saved_voices = db.query(SavedVoice).filter(SavedVoice.user_id == current_user.id).all()
    return {
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "username": current_user.username,
        },
        "saved_voices": [
            {
                "id": v.id,
                "voice_name": v.voice_name,
                "elevenlabs_voice_id": v.elevenlabs_voice_id,
                "language": v.language,
                "created_at": v.created_at.isoformat(),
            }
            for v in saved_voices
        ],
    }


# ── Save a voice to the user's account ────────────────────────────────────────

@app.post("/api/voices/save")
def save_voice(
    req: SaveVoiceRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save the current session clone to the user's account permanently."""
    key = _session_key(current_user.id, req.speaker)
    state = session_voice_state.get(key)

    if not state or state.get("clone_status") != "ready":
        raise HTTPException(400, "No ready clone found for this speaker. Calibrate first.")

    voice_id = state.get("clone_id")
    if not voice_id:
        raise HTTPException(400, "Clone ID missing")

    # Check voice limit (ElevenLabs free = 10 total)
    existing_count = db.query(SavedVoice).filter(SavedVoice.user_id == current_user.id).count()
    if existing_count >= 10:
        raise HTTPException(400, "Voice limit reached (10). Delete an old voice first.")

    voice = SavedVoice(
        user_id=current_user.id,
        elevenlabs_voice_id=voice_id,
        voice_name=req.voice_name,
        language=req.language,
    )
    db.add(voice)
    db.commit()
    db.refresh(voice)

    # Mark as saved so shutdown doesn't delete it
    state["is_saved"] = True

    return {
        "saved": True,
        "voice": {
            "id": voice.id,
            "voice_name": voice.voice_name,
            "elevenlabs_voice_id": voice.elevenlabs_voice_id,
            "language": voice.language,
        },
    }


@app.post("/api/voices/load")
def load_saved_voice(
    speaker: str = Form(...),
    voice_id: int = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Load a previously saved voice into the current session.
    Called on login when user selects their saved voice — skips calibration.
    """
    voice = db.query(SavedVoice).filter(
        SavedVoice.id == voice_id,
        SavedVoice.user_id == current_user.id
    ).first()

    if not voice:
        raise HTTPException(404, "Voice not found")

    # Inject into session state so TTS uses it immediately
    key = _session_key(current_user.id, speaker)
    session_voice_state[key] = {
        "samples": [], "total_seconds": 0.0,
        "clone_id": voice.elevenlabs_voice_id,
        "clone_status": "ready",
        "mime_type": "audio/webm",
        "is_saved": True,
    }

    # Update last_used_at
    voice.last_used_at = datetime.utcnow()
    db.commit()

    return {"loaded": True, "voice_name": voice.voice_name, "speaker": speaker}


@app.delete("/api/voices/{voice_id}")
async def delete_saved_voice(
    voice_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a saved voice from DB and ElevenLabs."""
    voice = db.query(SavedVoice).filter(
        SavedVoice.id == voice_id,
        SavedVoice.user_id == current_user.id,
    ).first()
    if not voice:
        raise HTTPException(404, "Voice not found")

    # Delete from ElevenLabs
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(
                f"https://api.elevenlabs.io/v1/voices/{voice.elevenlabs_voice_id}",
                headers={"xi-api-key": _eleven_key()},
            )
    except Exception as e:
        print(f"[voices] ElevenLabs delete warning: {e}")

    db.delete(voice)
    db.commit()
    return {"deleted": True, "voice_id": voice_id}


# ══════════════════════════════════════════════════════════════════════════════
# STT / TRANSLATION / TTS
# ══════════════════════════════════════════════════════════════════════════════

async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm", source_lang: Optional[str] = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        files = {"file": (filename, audio_bytes, "audio/webm")}
        data = {"model": "whisper-1", "response_format": "verbose_json"}
        if source_lang and source_lang != "auto":
            data["language"] = source_lang
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {_openai_key()}"},
            files=files, data=data,
        )
        resp.raise_for_status()
        result = resp.json()
        return {"text": result.get("text", ""), "detected_language": result.get("language", source_lang or "unknown")}


async def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    if not text.strip():
        return ""
    src_name = SUPPORTED_LANGUAGES.get(source_lang, source_lang)
    tgt_name = SUPPORTED_LANGUAGES.get(target_lang, target_lang)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {_openai_key()}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": f"You are a professional interpreter. Translate the following {src_name} text into natural, spoken {tgt_name}. Output ONLY the translation."},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3, "max_tokens": 500,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def synthesize_openai(text: str, voice: str = "alloy", speed: float = 1.0) -> bytes:
    if not text.strip():
        return b""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {_openai_key()}", "Content-Type": "application/json"},
            json={"model": "tts-1", "input": text, "voice": voice, "response_format": "mp3", "speed": speed},
        )
        resp.raise_for_status()
        return resp.content


async def synthesize_elevenlabs(text: str, voice_id: str) -> bytes:
    if not text.strip():
        return b""
    async with httpx.AsyncClient(timeout=40) as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": _eleven_key(), "Content-Type": "application/json", "Accept": "audio/mpeg"},
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.82, "style": 0.20, "use_speaker_boost": True},
            },
        )
        resp.raise_for_status()
        return resp.content


async def build_voice_clone(key: str) -> None:
    state = session_voice_state[key]
    state["clone_status"] = "building"
    try:
        mime = state.get("mime_type", "audio/webm")
        ext  = "mp4" if "mp4" in mime or "aac" in mime else "webm"
        files = [("files", (f"sample_{i}.{ext}", chunk, mime)) for i, chunk in enumerate(state["samples"])]
        data  = {"name": f"LangWarp Clone {key}", "description": "Auto-captured voice clone"}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.elevenlabs.io/v1/voices/add",
                headers={"xi-api-key": _eleven_key()},
                files=files, data=data,
            )
            resp.raise_for_status()
            state["clone_id"] = resp.json()["voice_id"]
            state["clone_status"] = "ready"
            print(f"[clone] {key} → {state['clone_id']} READY")
    except Exception as e:
        state["clone_status"] = "failed"
        print(f"[clone] {key} FAILED: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"[clone] ElevenLabs said: {e.response.text}")


async def synthesize(text: str, state: dict, speaker: str, speed: float = 1.0) -> tuple:
    if state.get("clone_status") == "ready" and state.get("clone_id"):
        audio = await synthesize_elevenlabs(text, state["clone_id"])
        return audio, "cloned"
    fallback = OPENAI_FALLBACK_VOICES.get(speaker, "alloy")
    audio = await synthesize_openai(text, fallback, speed)
    return audio, "fallback"


# ── Calibration ────────────────────────────────────────────────────────────────

@app.post("/api/calibrate")
async def calibrate_voice(
    audio: UploadFile = File(...),
    speaker: str = Form("speaker_a"),
    current_user: Optional[User] = Depends(get_optional_user),
):
    audio_bytes = await audio.read()
    user_id = current_user.id if current_user else None
    key = _session_key(user_id, speaker)

    mime_type = audio.content_type or "audio/webm"
    if mime_type == "application/octet-stream":
        mime_type = "audio/webm"
    print(f"[calibrate] {key} mime: {mime_type}")

    session_voice_state[key] = {
        "samples": [audio_bytes],
        "total_seconds": _estimate_duration(audio_bytes),
        "clone_id": None,
        "clone_status": "building",
        "mime_type": mime_type,
        "is_saved": False,
    }

    await build_voice_clone(key)
    state = session_voice_state[key]

    return {
        "speaker": speaker,
        "clone_status": state["clone_status"],
        "clone_id": state.get("clone_id"),
        "session_key": key,
    }


# ── Main translation endpoint ──────────────────────────────────────────────────

@app.post("/api/translate-voice")
async def translate_voice(
    audio: UploadFile = File(...),
    source_lang: str = Form("auto"),
    target_lang: str = Form("es"),
    speaker: str = Form("speaker_a"),
    speed: float = Form(1.0),
    current_user: Optional[User] = Depends(get_optional_user),
):
    audio_bytes = await audio.read()
    user_id = current_user.id if current_user else None
    state = _get_or_create_state(user_id, speaker)

    # Transcribe
    transcription = await transcribe_audio(
        audio_bytes,
        filename=audio.filename or "audio.webm",
        source_lang=None if source_lang == "auto" else source_lang,
    )
    original_text  = transcription["text"]
    detected_lang  = transcription["detected_language"]

    if not original_text.strip():
        return {"original_text": "", "translated_text": "", "detected_language": detected_lang,
                "target_language": target_lang, "audio_base64": "", "audio_mime": "audio/mpeg", "voice_mode": "none"}

    # Translate
    effective_source = detected_lang if source_lang == "auto" else source_lang
    translated_text  = await translate_text(original_text, effective_source, target_lang)

    # Synthesize
    audio_data, voice_mode = await synthesize(translated_text, state, speaker, speed)
    audio_b64 = base64.b64encode(audio_data).decode("utf-8")

    return {
        "original_text": original_text,
        "translated_text": translated_text,
        "detected_language": detected_lang,
        "target_language": target_lang,
        "audio_base64": audio_b64,
        "audio_mime": "audio/mpeg",
        "voice_mode": voice_mode,
    }


# ── Utilities ──────────────────────────────────────────────────────────────────

@app.get("/api/languages")
def get_languages():
    return SUPPORTED_LANGUAGES

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        "elevenlabs_key_set": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "active_sessions": len(session_voice_state),
    }

if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")