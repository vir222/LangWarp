# LangWarp 🌐🎙️
### Real-Time AI Voice Translation — Speak in any language, sound like yourself.

LangWarp is a full-stack Progressive Web App that translates your speech across 12 languages in real time while preserving your voice using ElevenLabs voice cloning. Built with a FastAPI backend and deployed as a PWA accessible on iOS and desktop.

---

## ✨ Features

- 🎤 **Real-time voice translation** across 12 languages
- 🔊 **Voice preservation** — output audio sounds like you, not a robot
- 🔐 **User authentication** with JWT tokens and bcrypt password hashing
- 💾 **Persistent voice profiles** stored across sessions via SQLite
- 📱 **PWA** — installable on iOS, works in the browser with no app store needed

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python) |
| Speech-to-Text | OpenAI Whisper |
| Translation | GPT-4o-mini |
| Voice Cloning | ElevenLabs API |
| Database | SQLite via SQLAlchemy |
| Auth | JWT tokens + bcrypt |
| Frontend | Progressive Web App (PWA) |
| Audio | MediaRecorder API (cross-platform) |

---

## 🔄 Pipeline

```
User Speech → Whisper (STT) → GPT-4o-mini (Translation) → ElevenLabs (Voice Cloning) → Translated Audio Output
```

---

## 📸 Screenshots

> Add your screenshots here — drag and drop images into this file when editing on GitHub
> Suggested: home screen, recording in progress, translated audio playback

---

## 🚀 Getting Started

### Prerequisites
- Python 3.9+
- OpenAI API key
- ElevenLabs API key

### Installation

```bash
# Clone the repo
git clone https://github.com/vir222/langwarp.git
cd langwarp

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Add your API keys to .env

# Run the backend
uvicorn main:app --reload
```

Then open `index.html` in your browser or add to home screen on iOS for the full PWA experience.

---

## 🌍 Supported Languages

English, Spanish, French, German, Italian, Portuguese, Japanese, Korean, Chinese (Mandarin), Arabic, Hindi, Russian

---

## 📁 Project Structure

```
langwarp/
├── main.py              # FastAPI app and routes
├── auth.py              # JWT authentication
├── database.py          # SQLAlchemy models
├── requirements.txt
├── static/
│   ├── index.html
│   ├── app.js
│   └── style.css
└── README.md
```

---

## 👤 Author

**Vir Vaidya** — vv275@rutgers.edu  
M.S. Computer Engineering, Rutgers University
