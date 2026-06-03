import base64
from datetime import datetime
import json
from io import BytesIO
import os
import threading
import time
import traceback
import uuid
import zipfile

import firebase_admin
from firebase_admin import credentials, firestore, storage
from flask import Flask, jsonify, render_template, request
import numpy as np
from PIL import Image
import requests

# =========================================================
# INITIALIZATION & FLASK APP
# =========================================================
app = Flask(__name__)
heartbeat_started = False

# =========================================================
# ENVIRONMENT VARIABLES & VALIDATION
# =========================================================
HUB_FIREBASE_KEY = os.environ.get("HUB_FIREBASE_KEY")
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
SERVER_ID = os.environ.get("SERVER_ID")
WORKER_WEBHOOK_URL = os.environ.get("WORKER_WEBHOOK_URL")
LIFF_ID = os.environ.get("LIFF_ID")

required_env = {
    "HUB_FIREBASE_KEY": HUB_FIREBASE_KEY,
    "WORKER_FIREBASE_KEY": WORKER_FIREBASE_KEY,
    "LINE_CHANNEL_ACCESS_TOKEN": LINE_CHANNEL_ACCESS_TOKEN,
    "SERVER_ID": SERVER_ID,
    "WORKER_WEBHOOK_URL": WORKER_WEBHOOK_URL,
    "LIFF_ID": LIFF_ID,
}

for key, value in required_env.items():
    if not value:
        raise RuntimeError(f"Missing environment variable: {key}")

# =========================================================
# FIREBASE SERVICES SETUPS
# =========================================================
# Setup HUB DB
hub_cred = credentials.Certificate(json.loads(HUB_FIREBASE_KEY))
hub_app = firebase_admin.initialize_app(hub_cred, name="hub")
hub_db = firestore.client(hub_app)

# Setup WORKER DB & STORAGE
worker_cred = credentials.Certificate(json.loads(WORKER_FIREBASE_KEY))
worker_app = firebase_admin.initialize_app(
    worker_cred,
    {"storageBucket": "basework-51f3b.firebasestorage.app"},
    name="worker"
)
worker_db = firestore.client(worker_app)
bucket = storage.bucket(app=worker_app)

# =========================================================
# LINE API CONFIGURATION
# =========================================================
LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_API = "https://api.line.me/v2/bot/message/push"
LINE_HEADERS = {
    "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

# =========================================================
# HEARTBEAT SYSTEM
# =========================================================
def heartbeat_loop():
    print("🔥 HEARTBEAT LOOP STARTED")
    while True:
        try:
            save_data = {
                "server_id": SERVER_ID,
                "status": "online",
                "load_score": 0,
                "cloud_url": WORKER_WEBHOOK_URL,
                "last_heartbeat": int(time.time()),
            }

            hub_db.collection("hub_system") \
                  .document("server_pool") \
                  .collection("servers") \
                  .document(SERVER_ID) \
                  .set(save_data, merge=True)

            print("✅ HEARTBEAT OK")
        except Exception:
            print("❌ HEARTBEAT ERROR")
            traceback.print_exc()

        time.sleep(30)  # Loop every 30 seconds

def start_heartbeat_once():
    global heartbeat_started
    if heartbeat_started:
        return

    heartbeat_started = True
    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()
    print("🚀 HEARTBEAT STARTED")

# =========================================================
# LINE HELPERS
# =========================================================
def reply_message(reply_token, text):
    try:
        payload = {
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text}],
        }
        requests.post(LINE_REPLY_API, headers=LINE_HEADERS, json=payload, timeout=10)
    except Exception as e:
        print("Reply error:", e)

def push_message(user_id, text):
    try:
        payload = {
            "to": user_id,
            "messages": [{"type": "text", "text": text}],
        }
        requests.post(LINE_PUSH_API, headers=LINE_HEADERS, json=payload, timeout=10)
    except Exception as e:
        print("Push error:", e)

# =========================================================
# CORE ENDPOINTS
# =========================================================
@app.route("/")
def home():
    return f"{SERVER_ID} RUNNING"


@app.route("/check-register", methods=["POST"])
def check_register():
    try:
        body = request.get_json(silent=True) or {}
        user_id = body.get("user_id")

        if not user_id:
            return jsonify({"registered": False})

        doc = worker_db.collection("user").document(user_id).get()
        if not doc.exists:
            return jsonify({"registered": False})

        data = doc.to_dict()
        return jsonify({"registered": data.get("register", False)})
    except Exception:
        traceback.print_exc()
        return jsonify({"registered": False})


@app.route("/register-user", methods=["POST"])
def register_user():
    try:
        body = request.get_json(silent=True) or {}
        print("REGISTER BODY =", body)

        user_id = body.get("user_id")
        if not user_id:
            return jsonify({"status": "error", "message": "no user_id"}), 400

        worker_db.collection("user").document(user_id).set({
            "userId": user_id,
            "fullname": body.get("name", ""),
            "phone": body.get("phone", ""),
            "email": body.get("email", ""),
            "register": True,
            "worker_id": SERVER_ID,
            "created_at": datetime.utcnow(),
        })

        print("✅ USER SAVED")
        return jsonify({"status": "success", "message": "ลงทะเบียนสำเร็จ"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/main-route", methods=["POST"])
def main_route():
    try:
        body = request.get_json(silent=True) or {}

        print("=" * 50)
        print("MAIN ROUTE")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print("=" * 50)

        events = body.get("events", [])
        for event in events:
            if event.get("type") != "message":
                continue

            message = event.get("message", {})
            message_type = message.get("type")

            # -------------------------------------------------
            # Handle Text Message
            # -------------------------------------------------
            if message_type == "text":
                text = message.get("text", "").strip()
                user_id = event["source"]["userId"]
                reply_token = event.get("replyToken")

                print("TEXT =", text)

                user_ref = worker_db.collection("user").document(user_id)
                session_ref = user_ref.collection("dataset_session").document(user_id)

                # Command: Download
                if text.lower().startswith("download"):
                    parts = text.split(" ")
                    return download_dataset(event, parts)

                # Command: Reset Session
                if text.lower() == "reset":
                    session_ref.delete()
                    reply_message(reply_token, "ล้าง session แล้ว")
                    return jsonify({"status": "success"})

                # Command: View Session
                if text.lower() == "session":
                    session_doc = session_ref.get()
                    if not session_doc.exists:
                        reply_message(reply_token, "ไม่มี session")
                        return jsonify({"status": "error"})

                    data = session_doc.to_dict()
                    reply_msg = (
                        f"PROJECT: {data.get('project')}\n"
                        f"CLASS: {data.get('label')}\n"
                        f"SIZE: {data.get('resize_width')}x{data.get('resize_height')}"
                    )
                    reply_message(reply_token, reply_msg)
                    return jsonify({"status": "success"})

                # Parsing Format: project/class/230x230
                path_parts = text.split("/")
                if len(path_parts) < 3:
                    error_msg = (
                        "รูปแบบ:\nproject/class/230x230\n\n"
                        "ตัวอย่าง:\nimagenumber/5/224x224\nplant/rust/640x480"
                    )
                    reply_message(reply_token, error_msg)
                    return jsonify({"status": "error"})

                project_name = path_parts[0].strip().lower()
                class_name = path_parts[1].strip().lower()
                size_text = path_parts[2].strip().lower()

                if "x" not in size_text:
                    reply_message(reply_token, "ขนาดผิดรูปแบบ\nเช่น 224x224")
                    return jsonify({"status": "error"})

                try:
                    w, h = size_text.split("x")
                    resize_width = int(w)
                    resize_height = int(h)
                except ValueError:
                    reply_message(reply_token, "ขนาดไม่ถูกต้อง\nเช่น 224x224")
                    return jsonify({"status": "error"})

                if resize_width <= 0 or resize_height <= 0:
                    reply_message(reply_token, "ขนาดต้องมากกว่า 0")
                    return jsonify({"status": "error"})

                # Save Config into Dataset Session
                session_ref.set({
                    "project": project_name,
                    "label": class_name,
                    "resize_width": resize_width,
                    "resize_height": resize_height,
                    "mode": "universal",
                    "updated_at": datetime.utcnow(),
                })

                print("SESSION SAVED")
                success_msg = (
                    f"📦 DATASET READY\n\n"
                    f"PROJECT: {project_name}\n"
                    f"CLASS: {class_name}\n"
                    f"SIZE: {resize_width}x{resize_height}\n\n"
                    f"ส่งรูปได้ต่อเนื่อง"
                )
                reply_message(reply_token, success_msg)
                return jsonify({"status": "success"})

            # -------------------------------------------------
            # Handle Image Message
            # -------------------------------------------------
            elif message_type == "image":
                return handle_image(event)

        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


def download_dataset(event, parts):
    try:
        reply_token = event.get("replyToken")
        user_id = event.get("source", {}).get("userId")

        if len(parts) < 2:
            reply_message(reply_token, "รูปแบบ:\ndownload meter\ndownload meter water")
            return jsonify({"status": "error"})

        project_name = parts[1].lower()

        if len(parts) == 2:
            label_name = "ALL"
            storage_prefix = f"{user_id}/{project_name}/"
        else:
            label_name = parts[2].lower()
            storage_prefix = f"{user_id}/{project_name}/{label_name}/"

        print("=" * 50)
        print("DOWNLOAD DATASET")
        print("USER =", user_id)
        print("PREFIX =", storage_prefix)
        print("=" * 50)

        blobs = list(bucket.list_blobs(prefix=storage_prefix))
        blobs = [b for b in blobs if not b.name.endswith("/")]  # Filter out directory entries
        print("TOTAL FILES =", len(blobs))

        if len(blobs) == 0:
            reply_message(reply_token, f"ไม่พบ dataset\n\n{storage_prefix}")
            return jsonify({"status": "error"})

        zip_filename = f"{project_name}_{label_name}.zip"
        zip_temp_path = f"/tmp/{zip_filename}"

        # Creating Zip File
        with zipfile.ZipFile(zip_temp_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for blob in blobs:
                filename = os.path.basename(blob.name)
                if not filename:
                    continue

                temp_file = f"/tmp/{filename}"
                blob.download_to_filename(temp_file)
                zipf.write(temp_file, arcname=blob.name.replace(f"{user_id}/", ""))

                if os.path.exists(temp_file):
                    os.remove(temp_file)

        # Upload Zip to storage
        zip_storage_path = f"{user_id}/downloads/{zip_filename}"
        zip_blob = bucket.blob(zip_storage_path)
        zip_blob.upload_from_filename(zip_temp_path, content_type="application/zip")
        zip_blob.make_public()
        zip_url = zip_blob.public_url

        if os.path.exists(zip_temp_path):
            os.remove(zip_temp_path)

        reply_msg = (
            f"DOWNLOAD READY\n\n"
            f"PROJECT: {project_name}\n"
            f"MODE: {label_name}\n"
            f"FILES: {len(blobs)}\n\n"
            f"{zip_url}"
        )
        reply_message(reply_token, reply_msg)
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        reply_message(event.get("replyToken"), f"DOWNLOAD ERROR\n\n{str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def handle_image(event):
    try:
        reply_token = event.get("replyToken")
        source = event.get("source", {})
        user_id = source.get("userId")
        message = event.get("message", {})

        # Fetch session configuration
        session_doc = worker_db.collection("user").document(user_id) \
                               .collection("dataset_session").document(user_id).get()

        if not session_doc.exists:
            reply_message(reply_token, "กรุณาพิมพ์:\nproject/class/224x224")
            return jsonify({"status": "error"})

        session_data = session_doc.to_dict()
        project_name = session_data.get("project")
        label_name = session_data.get("label")
        resize_width = int(session_data.get("resize_width", 224))
        resize_height = int(session_data.get("resize_height", 224))

        print("PROJECT =", project_name)
        print("LABEL =", label_name)
        print("SIZE =", resize_width, resize_height)

        # Download Binary Image from LINE API
        message_id = message.get("id")
        image_url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
        print("DOWNLOAD IMAGE")

        r = requests.get(
            image_url,
            headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"},
            timeout=30
        )
        print("LINE STATUS =", r.status_code)

        if r.status_code != 200:
            reply_message(reply_token, f"โหลดรูปไม่สำเร็จ\nSTATUS: {r.status_code}")
            return jsonify({"status": "error"})

        # Processing image (Resize & Convert to RGB)
        image = Image.open(BytesIO(r.content)).convert("RGB")
        image = image.resize((resize_width, resize_height))

        filename = f"{str(uuid.uuid4())}.jpg"
        temp_path = f"/tmp/{filename}"
        image.save(temp_path, format="JPEG")
        print("IMAGE SAVED =", temp_path)

        # Upload onto Cloud Storage
        storage_path = f"{user_id}/{project_name}/{label_name}/{filename}"
        print("STORAGE PATH =", storage_path)

        blob = bucket.blob(storage_path)
        blob.upload_from_filename(temp_path, content_type="image/jpeg")
        blob.make_public()
        public_url = blob.public_url
        print("UPLOAD SUCCESS\n", public_url)

        # Counting Current Dataset size
        storage_prefix = f"{user_id}/{project_name}/{label_name}/"
        blobs = list(bucket.list_blobs(prefix=storage_prefix))
        blobs = [b for b in blobs if not b.name.endswith("/")]
        total_images = len(blobs)
        print("TOTAL IMAGES =", total_images)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        reply_msg = (
            f"บันทึกรูปสำเร็จ\n\n"
            f"PROJECT: {project_name}\n"
            f"CLASS: {label_name}\n"
            f"SIZE: {resize_width}x{resize_height}\n"
            f"TOTAL: {total_images}\n\n"
            f"ส่งรูปต่อได้"
        )
        reply_message(reply_token, reply_msg)
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        reply_message(event.get("replyToken"), f"ERROR\n{str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():
    try:
        body = request.get_json(silent=True) or {}

        print("=" * 50)
        print("WORKER WEBHOOK")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print("=" * 50)

        events = body.get("events", [])
        for event in events:
            event_type = event.get("type")
            reply_token = event.get("replyToken")
            user_id = event.get("source", {}).get("userId")

            if not user_id:
                continue

            user_doc = worker_db.collection("user").document(user_id).get()
            if not user_doc.exists:
                continue

            user_data = user_doc.to_dict()
            fullname = user_data.get("fullname", "Unknown")

            # Worker Chat Command Handler
            if event_type == "message":
                text = event.get("message", {}).get("text", "")
                print("TEXT =", text)

                # Save incoming chat log
                worker_db.collection("chat_logs").add({
                    "user_id": user_id,
                    "fullname": fullname,
                    "text": text,
                    "timestamp": datetime.utcnow(),
                })

                if text.lower() == "ping":
                    reply_message(reply_token, "pong")
                elif text.lower() == "profile":
                    profile_msg = f"ชื่อ: {fullname}\nUSER: {user_id}\nWORKER: {SERVER_ID}"
                    reply_message(reply_token, profile_msg)
                else:
                    reply_message(reply_token, f"สวัสดี {fullname}\n\nคุณพิมพ์: {text}")

            elif event_type == "follow":
                push_message(user_id, "ยินดีต้อนรับ")

        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================================================
# APP RUNNER ENTRY POINT
# =========================================================
if __name__ == "__main__":
    # บูตระบบส่งสัญญาณ Heartbeat ตอนแอปเริ่มทำงานจริงเท่านั้น
    start_heartbeat_once()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)