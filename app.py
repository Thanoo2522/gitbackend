from flask import Flask, request, jsonify

import os
import json
import traceback
import requests
import time
import threading
import psutil 

from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)

# =========================================================
# HEARTBEAT STATE
# =========================================================
heartbeat_started = False

# =========================================================
# ENV
# =========================================================
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")
HUB_FIREBASE_KEY = os.environ.get("HUB_FIREBASE_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LIFF_ID = os.environ.get("LIFF_ID")
SERVER_ID = os.environ.get("SERVER_ID")
WORKER_WEBHOOK_URL = os.environ.get("WORKER_WEBHOOK_URL")

# =========================================================
# VALIDATION (กัน server พังเงียบ)
# =========================================================
if not WORKER_FIREBASE_KEY:
    raise RuntimeError("Missing WORKER_FIREBASE_KEY")

if not HUB_FIREBASE_KEY:
    raise RuntimeError("Missing HUB_FIREBASE_KEY")

if not SERVER_ID:
    raise RuntimeError("Missing SERVER_ID")

if not WORKER_WEBHOOK_URL:
    raise RuntimeError("Missing WORKER_WEBHOOK_URL")

# =========================================================
# FIREBASE (WORKER)
# =========================================================
worker_cred = credentials.Certificate(json.loads(WORKER_FIREBASE_KEY))
worker_app = firebase_admin.initialize_app(worker_cred, name="worker")
worker_db = firestore.client(worker_app)

# =========================================================
# FIREBASE (HUB)
# =========================================================
hub_cred = credentials.Certificate(json.loads(HUB_FIREBASE_KEY))
hub_app = firebase_admin.initialize_app(hub_cred, name="hub")
hub_db = firestore.client(hub_app)

# =========================================================
# LINE API
# =========================================================
LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_API = "https://api.line.me/v2/bot/message/push"

LINE_HEADERS = {
    "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# =========================================================
# HEARTBEAT LOOP (REAL METRICS)
# =========================================================
def heartbeat_loop():

    print("🔥 HEARTBEAT LOOP STARTED")

    while True:
        try:
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent

            load_score = (cpu * 0.5) + (ram * 0.5)

            save_data = {
                "server_id": SERVER_ID,
                "status": "online",
                "cpu": cpu,
                "ram": ram,
                "disk": disk,
                "load_score": load_score,
                "cloud_url": WORKER_WEBHOOK_URL,
                "last_heartbeat": int(time.time())
            }

            print("SENDING HEARTBEAT:", save_data)

            hub_db.collection("hub_system") \
                .document("server_pool") \
                .collection("servers") \
                .document(SERVER_ID) \
                .set(save_data, merge=True)

            print("✅ HEARTBEAT UPDATED")

        except Exception as e:
            print("❌ HEARTBEAT ERROR:", str(e))
            traceback.print_exc()

        time.sleep(30)

# =========================================================
# START HEARTBEAT (SAFE ONCE ONLY)
# =========================================================
def start_heartbeat():
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    print("🚀 HEARTBEAT THREAD STARTED")
# =========================================================
# HOME
# =========================================================
@app.route("/")
def home():
    return f"{SERVER_ID} RUNNING"

# =========================================================
# START HEARTBEAT BEFORE FIRST REQUEST
# =========================================================
@app.before_request
def ensure_heartbeat():
    start_heartbeat()

# =========================================================
# CHECK REGISTER
# =========================================================
@app.route("/check-register", methods=["POST"])
def check_register():

    try:
        body = request.get_json()

        user_id = body.get("user_id")

        if not user_id:
            return jsonify({"registered": False})

        doc = worker_db.collection("user").document(user_id).get()

        if not doc.exists:
            return jsonify({"registered": False})

        data = doc.to_dict()

        return jsonify({
            "registered": data.get("register", False)
        })

    except Exception:
        traceback.print_exc()
        return jsonify({"registered": False})

# =========================================================
# REPLY MESSAGE
# =========================================================
def reply_message(reply_token, text):

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    r = requests.post(
        LINE_REPLY_API,
        headers=LINE_HEADERS,
        json=payload,
        timeout=10
    )

    print("REPLY:", r.status_code, r.text)

# =========================================================
# PUSH MESSAGE
# =========================================================
def push_message(user_id, text):

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    r = requests.post(
        LINE_PUSH_API,
        headers=LINE_HEADERS,
        json=payload,
        timeout=10
    )

    print("PUSH:", r.status_code, r.text)

# =========================================================
# WORKER WEBHOOK
# =========================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    try:
        body = request.get_json()

        events = body.get("events", [])

        for event in events:

            event_type = event.get("type")
            reply_token = event.get("replyToken")

            source = event.get("source", {})
            user_id = source.get("userId")

            if not user_id:
                continue

            user_doc = worker_db.collection("user").document(user_id).get()

            if not user_doc.exists:
                continue

            user_data = user_doc.to_dict()
            fullname = user_data.get("fullname", "Unknown")

            if event_type == "message":

                message = event.get("message", {})
                text = message.get("text", "")

                worker_db.collection("chat_logs").add({
                    "user_id": user_id,
                    "fullname": fullname,
                    "text": text,
                    "timestamp": datetime.utcnow()
                })

                if text.lower() == "ping":
                    reply_message(reply_token, "pong")

                elif text.lower() == "profile":
                    reply_message(reply_token,
                        f"ชื่อ: {fullname}\nUSER: {user_id}"
                    )

                else:
                    reply_message(reply_token,
                        f"สวัสดี {fullname}\n{text}"
                    )

            elif event_type == "follow":
                push_message(user_id, "ยินดีต้อนรับ")

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================================================
# REGISTER USER
# =========================================================
@app.route("/register-user", methods=["POST"])
def register_user():

    try:
        body = request.get_json()

        user_id = body.get("user_id")

        if not user_id:
            return jsonify({"status": "error", "message": "no user_id"})

        worker_db.collection("user").document(user_id).set({
            "userId": user_id,
            "fullname": body.get("name"),
            "phone": body.get("phone"),
            "email": body.get("email"),
            "register": True,
            "created_at": datetime.utcnow()
        })

        return jsonify({
            "status": "success",
            "message": "ลงทะเบียนสำเร็จ"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    start_heartbeat()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        debug=True
    )