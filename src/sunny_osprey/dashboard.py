"""
Sunny Osprey Dashboard - FastAPI Web Server

Provides:
- REST API for event ingestion from webcam_capture.py
- WebSocket for real-time event streaming to browser
- Serve the static web dashboard (dashboard/index.html)
- Live webcam frame endpoint for browser preview
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


# ── App Setup ────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Sunny Osprey Dashboard",
    description="Real-time AI security monitoring dashboard",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-Memory State ──────────────────────────────────────────────────────────

# Circular buffer of the last 100 events
events_buffer: deque = deque(maxlen=100)

# Active WebSocket connections (for live push)
active_websockets: List[WebSocket] = []

# Latest webcam frame (base64 JPEG)
latest_frame: Optional[str] = None

# System stats
stats = {
    "total_events": 0,
    "suspicious_events": 0,
    "normal_events": 0,
    "pipeline_status": "waiting",
    "last_event_time": None,
}

# Events storage directory
EVENTS_STORE = os.getenv("EVENTS_STORE_PATH", "/app/events_store")
os.makedirs(EVENTS_STORE, exist_ok=True)

# Dashboard static files directory
# Resolution order: relative to this file → /app/dashboard (Docker) → cwd/dashboard
_candidates = [
    Path(__file__).parent.parent.parent / "dashboard",  # local dev: project_root/dashboard
    Path("/app/dashboard"),                               # Docker WORKDIR
    Path.cwd() / "dashboard",                            # fallback
]
DASHBOARD_DIR = next((p for p in _candidates if p.exists()), _candidates[0])


# ── Request/Response Models ──────────────────────────────────────────────────

class EventPayload(BaseModel):
    """Incoming event from webcam_capture.py"""
    event_id: Optional[str] = None
    suspicious: str = "No"
    description: str = ""
    timestamp: Optional[str] = None
    source: Optional[str] = "webcam"
    video_path: Optional[str] = None
    error: Optional[str] = None


class FramePayload(BaseModel):
    """Incoming webcam frame for live preview"""
    frame: str  # base64-encoded JPEG


# ── WebSocket Manager ────────────────────────────────────────────────────────

async def broadcast_to_websockets(message: dict):
    """Send a message to all connected WebSocket clients."""
    dead = []
    for ws in active_websockets:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        active_websockets.remove(ws)


# ── API Endpoints ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the main dashboard HTML."""
    html_path = DASHBOARD_DIR / "index.html"
    if html_path.exists():
        async with aiofiles.open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=await f.read())
    return HTMLResponse(
        content="<h1>Dashboard not found</h1><p>Ensure dashboard/index.html exists.</p>",
        status_code=404,
    )


@app.post("/api/events")
async def receive_event(payload: EventPayload):
    """
    Receive a new security event from the webcam pipeline.
    Stores it, updates stats, and broadcasts to WebSocket clients.
    """
    global stats

    event = payload.dict()
    if not event.get("timestamp"):
        event["timestamp"] = datetime.utcnow().isoformat()
    if not event.get("event_id"):
        import uuid
        event["event_id"] = f"evt_{uuid.uuid4().hex[:8]}"

    is_suspicious = event.get("suspicious", "No").lower() == "yes"

    # Update stats
    stats["total_events"] += 1
    stats["last_event_time"] = event["timestamp"]
    stats["pipeline_status"] = "active"
    if is_suspicious:
        stats["suspicious_events"] += 1
    else:
        stats["normal_events"] += 1

    # Add to in-memory buffer
    events_buffer.appendleft(event)

    # Persist to disk
    event_file = os.path.join(EVENTS_STORE, f"{event['event_id']}.json")
    async with aiofiles.open(event_file, "w") as f:
        await f.write(json.dumps(event, indent=2))

    # Broadcast to all connected WebSocket clients
    await broadcast_to_websockets({
        "type": "new_event",
        "event": event,
        "stats": stats,
    })

    logger.info(
        f"Event received: {event['event_id']} | "
        f"suspicious={event.get('suspicious')} | "
        f"clients={len(active_websockets)}"
    )

    return {"status": "ok", "event_id": event["event_id"]}


@app.post("/api/frame")
async def receive_frame(payload: FramePayload):
    """Receive latest webcam frame for live preview."""
    global latest_frame
    latest_frame = payload.frame

    # Push to websocket clients (non-blocking)
    asyncio.create_task(broadcast_to_websockets({
        "type": "frame",
        "frame": payload.frame,
    }))

    return {"status": "ok"}


@app.get("/api/events")
async def get_events(limit: int = 50):
    """Get the most recent events."""
    return {"events": list(events_buffer)[:limit], "stats": stats}


@app.get("/api/stats")
async def get_stats():
    """Get system statistics."""
    return {
        **stats,
        "active_connections": len(active_websockets),
        "buffered_events": len(events_buffer),
    }


class VideoAnalyzeRequest(BaseModel):
    video_path: str
    notify_alerts: bool = True


@app.post("/api/analyze-video")
async def analyze_video_endpoint(req: VideoAnalyzeRequest):
    """Trigger anomaly detection on a video file from the dashboard."""
    video_path = req.video_path
    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail=f"Video file not found: {video_path}")

    try:
        from sunny_osprey.video_processor import VideoFileProcessor
        processor = VideoFileProcessor()
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None, processor.process_video, video_path, req.notify_alerts
        )
        return res
    except Exception as e:
        logger.error(f"Video analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Temp directory for uploaded video files
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "sunny_osprey_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.post("/api/upload-video")
async def upload_and_analyze_video(file: UploadFile = File(...)):
    """
    Accept a video file upload from the browser, save to temp,
    and run anomaly detection on it.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Save uploaded file to temp directory
    safe_name = file.filename.replace("..", "_").replace("/", "_").replace("\\", "_")
    temp_path = os.path.join(UPLOAD_DIR, safe_name)

    try:
        async with aiofiles.open(temp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                await f.write(chunk)

        logger.info(f"Uploaded video saved: {temp_path} ({os.path.getsize(temp_path)} bytes)")

        from sunny_osprey.video_processor import VideoFileProcessor
        processor = VideoFileProcessor()
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None, processor.process_video, temp_path, True
        )
        return res

    except Exception as e:
        logger.error(f"Video upload/analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass


@app.get("/api/frame/latest")
async def get_latest_frame():
    """Get the latest webcam frame as base64."""
    if latest_frame is None:
        raise HTTPException(status_code=404, detail="No frame available yet")
    return {"frame": latest_frame}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time dashboard updates."""
    await websocket.accept()
    active_websockets.append(websocket)
    logger.info(f"WebSocket client connected. Total: {len(active_websockets)}")

    try:
        # Send current state immediately on connection
        await websocket.send_json({
            "type": "init",
            "events": list(events_buffer)[:20],
            "stats": stats,
            "frame": latest_frame,
        })

        # Keep connection alive and listen for pings
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        if websocket in active_websockets:
            active_websockets.remove(websocket)


@app.delete("/api/events")
async def clear_events():
    """Clear all events and reset stats (fresh start mid-session)."""
    global stats, latest_frame
    events_buffer.clear()
    stats = {
        "total_events": 0,
        "suspicious_events": 0,
        "normal_events": 0,
        "pipeline_status": "waiting",
        "last_event_time": None,
    }

    # Wipe persisted events from disk
    if os.path.exists(EVENTS_STORE):
        for ef in Path(EVENTS_STORE).glob("*.json"):
            try:
                ef.unlink()
            except Exception:
                pass

    # Notify all connected WebSocket clients to reset their UI
    await broadcast_to_websockets({
        "type": "clear",
        "stats": stats,
    })

    logger.info("🗑️ All events cleared — fresh session started.")
    return {"status": "ok", "message": "All events cleared"}


# Camera publish page with the settings the pipeline needs: H.264 (browsers default to AV1, which
# OpenCV here cannot decode), 720p at 15 fps, no microphone. Same link as scripts/start.ps1.
CAMERA_PUBLISH_URL = (
    "http://localhost:8889/webcam/publish?video-codec=h264%2F90000&video-width=1280"
    "&video-height=720&video-framerate=15&video-bitrate=2500&audio-device=none"
)


@app.get("/api/links")
async def get_links():
    """Links for the header buttons: camera publish page and the private ntfy alert topic."""
    alerts_url = None
    try:
        import yaml
        with open(os.getenv("SUNNY_OSPREY_CONFIG", "/app/sunny-osprey-config.yaml"), encoding="utf-8") as f:
            ntfy = (yaml.safe_load(f) or {}).get("alerts", {}).get("ntfy", {})
        if ntfy.get("topic") and ntfy.get("server"):
            alerts_url = f"{ntfy['server'].rstrip('/')}/{ntfy['topic']}"
    except Exception as e:
        logger.warning(f"Could not read alert topic from config: {e}")
    return {"camera": CAMERA_PUBLISH_URL, "alerts": alerts_url}


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    ollama_ok = False
    try:
        import requests as _requests
        path = "/health" if os.getenv("LLM_API", "ollama").lower() == "openai" else "/api/tags"
        resp = _requests.get(
            f"{os.getenv('OLLAMA_HOST', 'http://ollama:11434')}{path}",
            timeout=3,
        )
        ollama_ok = resp.status_code == 200
    except Exception:
        pass

    return {
        "status": "healthy",
        "dashboard": "ok",
        "ollama": "ok" if ollama_ok else "unavailable",
        "active_ws_connections": len(active_websockets),
    }


# ── Startup Events ────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Clear old events and start fresh on every new session."""
    logger.info("🚀 Sunny Osprey Dashboard starting up...")

    # Clear old events from disk so every session starts fresh
    if os.path.exists(EVENTS_STORE):
        old_files = list(Path(EVENTS_STORE).glob("*.json"))
        for ef in old_files:
            try:
                ef.unlink()
            except Exception as e:
                logger.warning(f"Could not delete old event file {ef}: {e}")
        if old_files:
            logger.info(f"🗑️ Cleared {len(old_files)} old events from previous session.")

    logger.info("✅ Dashboard ready. Starting with a clean session.")


# ── Mount Static Files ────────────────────────────────────────────────────────

static_dir = DASHBOARD_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
