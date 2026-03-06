"""
AIS WebSocket → REST Bridge (v3 - Timeout Fixed)
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
ws_status      = "starting"
ws_error       = ""

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":            "running",
        "ws_status":         ws_status,
        "ws_error":          ws_error,
        "buffer_size":       len(message_buffer),
        "messages_received": message_count,
        "last_updated":      str(last_updated),
        "api_key_set":       bool(API_KEY),
        "api_key_prefix":    API_KEY[:8] if API_KEY else "NOT SET"
    })

@app.route("/snapshot", methods=["GET"])
def snapshot():
    with buffer_lock:
        messages = list(message_buffer)
        message_buffer.clear()
    return jsonify({
        "count":        len(messages),
        "last_updated": str(last_updated),
        "messages":     messages
    })

async def listen_ais():
    global last_updated, message_count, ws_status, ws_error

    subscribe_message = {
        "APIKey": API_KEY,
        "BoundingBoxes": [[[-90, -180], [90, 180]]],
        "FilterMessageTypes": [
            "PositionReport",
            "ShipStaticData",
            "StandardClassBPositionReport"
        ]
    }

    print(f"🔑 API Key: {API_KEY[:8]}..." if API_KEY else "❌ NO API KEY")
    print("🌍 Starting global AIS connection...")

    retry_wait = 5

    while True:
        try:
            ws_status = "connecting"
            print(f"📡 Connecting to aisstream.io...")

            # ── Use default SSL (no custom context) ──────────────
            async with websockets.connect(
                "wss://stream.aisstream.io/v0/stream",
                ping_interval=None,     # aisstream manages keepalive
                close_timeout=10,
                max_size=None,
                open_timeout=30,        # 30s timeout — enough for Render
                extra_headers={
                    "User-Agent": "AISBridge/1.0"
                }
            ) as ws:

                ws_status  = "connected"
                ws_error   = ""
                retry_wait = 5          # reset backoff on success
                print("✅ Connected! Sending subscription...")

                # Send subscription immediately
                await ws.send(json.dumps(subscribe_message))
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

        except websockets.exceptions.InvalidStatusCode as e:
            ws_status = "rejected"
            ws_error  = f"HTTP {e.status_code} - invalid API key or banned"
            print(f"❌ {ws_error}")
            await asyncio.sleep(30)    # longer wait for auth errors

        except asyncio.TimeoutError:
            ws_status = "timeout"
            ws_error  = "Connection timed out"
            print(f"⚠️  Timeout — retrying in {retry_wait}s...")
            await asyncio.sleep(retry_wait)
            retry_wait = min(retry_wait * 2, 60)  # exponential backoff

        except OSError as e:
            ws_status = "network_error"
            ws_error  = str(e)
            print(f"⚠️  Network error: {e} — retrying in {retry_wait}s...")
            await asyncio.sleep(retry_wait)
            retry_wait = min(retry_wait * 2, 60)

        except Exception as e:
            ws_status = f"error"
            ws_error  = f"{type(e).__name__}: {str(e)}"
            print(f"⚠️  {ws_error} — retrying in {retry_wait}s...")
            await asyncio.sleep(retry_wait)
            retry_wait = min(retry_wait * 2, 60)

def start_websocket():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(listen_ais())

if __name__ == "__main__":
    print(f"🔑 API_KEY: {'SET (' + API_KEY[:8] + '...)' if API_KEY else 'NOT SET ❌'}")
    ws_thread = threading.Thread(target=start_websocket, daemon=True)
    ws_thread.start()
    print(f"🚀 REST API → http://0.0.0.0:{PORT}/snapshot")
    app.run(host="0.0.0.0", port=PORT, debug=False)