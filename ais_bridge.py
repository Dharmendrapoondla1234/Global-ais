"""
ais_bridge.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WebSocket → REST bridge for Google Apps Script

1. Connects to aisstream.io WebSocket permanently
2. Buffers last N messages in memory
3. Exposes GET /snapshot → returns buffered messages as JSON
4. Apps Script polls GET /snapshot every 1 minute

Deploy on: Cloud Run / Render / Railway (free tier)

pip install flask websockets asyncio threading python-dotenv
python ais_bridge.py
"""

import asyncio
import websockets
import json
import threading
import os
from datetime import datetime, timezone
from collections import deque
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

API_KEY    = os.getenv("AISSTREAM_API_KEY", "")
PORT       = int(os.getenv("PORT", "8080"))
MAX_BUFFER = int(os.getenv("MAX_BUFFER", "1000"))  # keep last 1000 messages

# ── Shared buffer (thread-safe deque) ─────────────────────────────
message_buffer = deque(maxlen=MAX_BUFFER)
buffer_lock    = threading.Lock()
last_updated   = None

# ── Flask REST API ─────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":       "ok",
        "buffer_size":  len(message_buffer),
        "last_updated": str(last_updated)
    })

@app.route("/snapshot", methods=["GET"])
def snapshot():
    """
    Apps Script calls this endpoint every minute.
    Returns all buffered messages and clears the buffer.
    """
    global last_updated
    with buffer_lock:
        messages = list(message_buffer)
        message_buffer.clear()  # clear after serving so no duplicates next call

    return jsonify({
        "count":        len(messages),
        "last_updated": str(last_updated),
        "messages":     messages
    })

# ── AIS WebSocket listener (runs in background thread) ────────────
async def listen_ais():
    global last_updated

    subscribe = {
        "APIKey":             API_KEY,
        "BoundingBoxes":      [[[-90, -180], [90, 180]]],  # global
        "FilterMessageTypes": ["PositionReport", "ShipStaticData",
                               "StandardClassBPositionReport"]
    }

    print(f"[AIS Bridge] Connecting to aisstream.io...")

    while True:
        try:
            async with websockets.connect(
                "wss://stream.aisstream.io/v0/stream",
                ping_interval=20,
                ping_timeout=30
            ) as ws:
                await ws.send(json.dumps(subscribe))
                print("[AIS Bridge] ✅ Connected — receiving global AIS stream")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        msg["_received_at"] = datetime.now(timezone.utc).isoformat()

                        with buffer_lock:
                            message_buffer.append(msg)

                        last_updated = datetime.now(timezone.utc).isoformat()

                    except Exception as e:
                        print(f"[AIS Bridge] Parse error: {e}")

        except Exception as e:
            print(f"[AIS Bridge] Disconnected: {e} — reconnecting in 5s...")
            await asyncio.sleep(5)

def start_websocket_thread():
    """Run the async WebSocket listener in a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(listen_ais())

# ── START ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Start WebSocket listener in background
    t = threading.Thread(target=start_websocket_thread, daemon=True)
    t.start()
    print(f"[AIS Bridge] 🌍 REST API running on port {PORT}")
    print(f"[AIS Bridge] 📡 Poll endpoint: GET /snapshot")
    # Start Flask
    app.run(host="0.0.0.0", port=PORT)