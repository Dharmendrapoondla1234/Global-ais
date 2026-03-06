"""
AIS WebSocket → REST Bridge
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Purpose
-------
Connects to AISStream WebSocket and exposes a REST API
so Google Apps Script can poll vessel data.

Flow
----
AISStream WebSocket
        ↓
Python bridge buffers messages
        ↓
Apps Script polls /snapshot every minute

Deploy on:
Cloud Run / Render / Railway / VPS

Install:
pip install flask websockets python-dotenv
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

# ─────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────

load_dotenv()

API_KEY    = os.getenv("AISSTREAM_API_KEY", "")
PORT       = int(os.getenv("PORT", "8080"))
MAX_BUFFER = int(os.getenv("MAX_BUFFER", "20000"))

# ─────────────────────────────────────────
# SHARED MEMORY BUFFER
# ─────────────────────────────────────────

message_buffer = deque(maxlen=MAX_BUFFER)
buffer_lock    = threading.Lock()
last_updated   = None
message_count  = 0

# ─────────────────────────────────────────
# FLASK REST API
# ─────────────────────────────────────────

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":            "running",
        "buffer_size":       len(message_buffer),
        "messages_received": message_count,
        "last_updated":      str(last_updated)
    })

@app.route("/snapshot", methods=["GET"])
def snapshot():
    global last_updated
    with buffer_lock:
        messages = list(message_buffer)
        message_buffer.clear()
    return jsonify({
        "count":        len(messages),
        "last_updated": str(last_updated),
        "messages":     messages
    })

# ─────────────────────────────────────────
# AIS WEBSOCKET LISTENER
# ─────────────────────────────────────────

async def listen_ais():
    global last_updated, message_count

    subscribe_message = {
        "APIKey": API_KEY,

        # ✅ CORRECT FORMAT: [[lat_min, lng_min], [lat_max, lng_max]]
        # OLD WRONG:  [[-180, -90], [180, 90]]  ← lng/lat swapped
        # FIXED:      [[-90, -180], [90, 180]]  ← lat first, lng second
        "BoundingBoxes": [
            [[-90, -180], [90, 180]]
        ],

        "FilterMessageTypes": [
            "PositionReport",
            "ShipStaticData",
            "StandardClassBPositionReport"
        ]
    }

    print("🌍 AIS Bridge starting — GLOBAL coverage...")
    print("📡 BoundingBox: lat[-90→90] lng[-180→180]")
    print("📡 Connecting to AISStream WebSocket...")

    while True:
        try:
            async with websockets.connect(
                "wss://stream.aisstream.io/v0/stream",
                ping_interval=20,
                ping_timeout=30,
                max_size=None
            ) as ws:

                print("✅ Connected to AISStream")
                await ws.send(json.dumps(subscribe_message))

                async for raw_message in ws:
                    try:
                        message = json.loads(raw_message)
                        message["_received_at"] = datetime.now(timezone.utc).isoformat()

                        with buffer_lock:
                            message_buffer.append(message)

                        last_updated   = datetime.now(timezone.utc).isoformat()
                        message_count += 1

                        if message_count % 1000 == 0:
                            print(f"📦 {message_count:,} messages received | buffer: {len(message_buffer):,}")

                    except Exception as parse_error:
                        print("Parse error:", parse_error)

        except Exception as connection_error:
            print("⚠️  Disconnected:", connection_error)
            print("🔁 Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

# ─────────────────────────────────────────
# BACKGROUND THREAD
# ─────────────────────────────────────────

def start_websocket():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(listen_ais())

# ─────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────

if __name__ == "__main__":
    ws_thread = threading.Thread(target=start_websocket, daemon=True)
    ws_thread.start()
    print("🚀 AIS Bridge REST API started")
    print(f"📡 Poll endpoint → http://localhost:{PORT}/snapshot")
    app.run(host="0.0.0.0", port=PORT)