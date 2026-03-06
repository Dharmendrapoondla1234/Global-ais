"""
AIS WebSocket → REST Bridge (FIXED)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
pip install flask websockets python-dotenv
"""

import asyncio
import websockets
import ssl
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
MAX_BUFFER = int(os.getenv("MAX_BUFFER", "20000"))

message_buffer = deque(maxlen=MAX_BUFFER)
buffer_lock    = threading.Lock()
last_updated   = None
message_count  = 0
ws_status      = "connecting"

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":            "running",
        "ws_status":         ws_status,
        "buffer_size":       len(message_buffer),
        "messages_received": message_count,
        "last_updated":      str(last_updated),
        "api_key_set":       bool(API_KEY)
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

async def listen_ais():
    global last_updated, message_count, ws_status

    # ── SSL context — disable verify for Render environment ──────
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = False
    ssl_context.verify_mode    = ssl.CERT_NONE

    subscribe_message = {
        "APIKey": API_KEY,
        "BoundingBoxes": [
            [[-90, -180], [90, 180]]   # ✅ lat first, lng second — global
        ],
        "FilterMessageTypes": [
            "PositionReport",
            "ShipStaticData",
            "StandardClassBPositionReport"
        ]
    }

    print(f"🔑 API Key set: {bool(API_KEY)} ({API_KEY[:8]}...)" if API_KEY else "❌ NO API KEY SET")
    print("🌍 Connecting to aisstream.io — global coverage...")

    while True:
        try:
            ws_status = "connecting"
            async with websockets.connect(
                "wss://stream.aisstream.io/v0/stream",
                ssl=ssl_context,
                ping_interval=None,   # ← disable ping, aisstream manages keepalive
                open_timeout=10,
                max_size=None
            ) as ws:

                ws_status = "connected"
                print("✅ WebSocket connected — sending subscription...")

                # ── Must send within 3 seconds ──────────────────
                await asyncio.wait_for(
                    ws.send(json.dumps(subscribe_message)),
                    timeout=3.0
                )
                print("📡 Subscribed — receiving global AIS stream...")

                async for raw_message in ws:
                    try:
                        message = json.loads(raw_message)
                        message["_received_at"] = datetime.now(timezone.utc).isoformat()

                        with buffer_lock:
                            message_buffer.append(message)

                        last_updated   = datetime.now(timezone.utc).isoformat()
                        message_count += 1

                        if message_count % 500 == 0:
                            print(f"📦 {message_count:,} messages | buffer: {len(message_buffer):,}")

                    except Exception as e:
                        print(f"Parse error: {e}")

        except asyncio.TimeoutError:
            ws_status = "timeout"
            print("⚠️  Connection timeout — retrying in 5s...")
            await asyncio.sleep(5)

        except websockets.exceptions.InvalidStatusCode as e:
            ws_status = f"rejected:{e.status_code}"
            print(f"❌ Connection rejected HTTP {e.status_code} — check API key")
            await asyncio.sleep(10)

        except Exception as e:
            ws_status = f"error:{type(e).__name__}"
            print(f"⚠️  Disconnected: {type(e).__name__}: {e}")
            print("🔁 Reconnecting in 5s...")
            await asyncio.sleep(5)

def start_websocket():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(listen_ais())

if __name__ == "__main__":
    print(f"🔑 API_KEY loaded: {'YES - ' + API_KEY[:8] + '...' if API_KEY else 'NO - SET AISSTREAM_API_KEY ENV VAR'}")

    ws_thread = threading.Thread(target=start_websocket, daemon=True)
    ws_thread.start()

    print(f"🚀 REST API → http://0.0.0.0:{PORT}/snapshot")
    app.run(host="0.0.0.0", port=PORT, debug=False)