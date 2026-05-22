from flask import Flask, request, jsonify

import os
import json
import traceback
import requests
import time
import threading

from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore, storage
from PIL import Image
from io import BytesIO
import uuid
import base64

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
HUB_FIREBASE_KEY = os.environ.get(
    "HUB_FIREBASE_KEY"
)

WORKER_FIREBASE_KEY = os.environ.get(
    "WORKER_FIREBASE_KEY"
)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LINE_CHANNEL_ACCESS_TOKEN"
)

SERVER_ID = os.environ.get(
    "SERVER_ID"
)

WORKER_WEBHOOK_URL = os.environ.get(
    "WORKER_WEBHOOK_URL"
)

# =========================================================
# VALIDATION
# =========================================================
required_env = {

    "HUB_FIREBASE_KEY":
        HUB_FIREBASE_KEY,

    "WORKER_FIREBASE_KEY":
        WORKER_FIREBASE_KEY,

    "LINE_CHANNEL_ACCESS_TOKEN":
        LINE_CHANNEL_ACCESS_TOKEN,

    "SERVER_ID":
        SERVER_ID,

    "WORKER_WEBHOOK_URL":
        WORKER_WEBHOOK_URL
}

for k, v in required_env.items():

    if not v:
        raise RuntimeError(f"Missing {k}")

# =========================================================
# FIREBASE
# =========================================================
# FIREBASE
# =========================================================

# ---------------------------------------------------------
# HUB DB
# ---------------------------------------------------------
hub_cred = credentials.Certificate(
    json.loads(HUB_FIREBASE_KEY)
)

hub_app = firebase_admin.initialize_app(

    hub_cred,

    name="hub"
)

hub_db = firestore.client(
    hub_app
)

# ---------------------------------------------------------
# WORKER DB + STORAGE
# ---------------------------------------------------------
worker_cred = credentials.Certificate(
    json.loads(WORKER_FIREBASE_KEY)
)

worker_app = firebase_admin.initialize_app(

    worker_cred,

    {
        "storageBucket":
            "basework-51f3b.firebasestorage.app"
    },

    name="worker"
)

worker_db = firestore.client(
    worker_app
)

# IMPORTANT
bucket = storage.bucket(
    app=worker_app
)

# =========================================================
# LINE API
# =========================================================
LINE_REPLY_API = (
    "https://api.line.me/v2/bot/message/reply"
)

LINE_PUSH_API = (
    "https://api.line.me/v2/bot/message/push"
)

LINE_HEADERS = {

    "Authorization":
        f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",

    "Content-Type":
        "application/json"
}

# =========================================================
# HEARTBEAT LOOP กระตุ้กไปที่ HUB  ให้รู้ว่ายังonline อยู่
# =========================================================
def heartbeat_loop():

    print("🔥 HEARTBEAT LOOP STARTED")

    while True:

        try:

            save_data = {

                "server_id":
                    SERVER_ID,

                "status":
                    "online",

                "load_score":
                    0,

                "cloud_url":
                    WORKER_WEBHOOK_URL,

                "last_heartbeat":
                    int(time.time())
            }

            hub_db.collection("hub_system") \
                .document("server_pool") \
                .collection("servers") \
                .document(SERVER_ID) \
                .set(save_data, merge=True)

            print("✅ HEARTBEAT OK")

        except Exception as e:

            print("❌ HEARTBEAT ERROR")

            traceback.print_exc()

        # LOOP EVERY 30 SEC
        time.sleep(30)

# =========================================================
# START HEARTBEAT
# =========================================================
def start_heartbeat_once():

    global heartbeat_started

    if heartbeat_started:
        return

    heartbeat_started = True

    threading.Thread(

        target=heartbeat_loop,

        daemon=True

    ).start()

    print("🚀 HEARTBEAT STARTED")

# =========================================================
# START HEARTBEAT ON BOOT
# =========================================================
start_heartbeat_once()

# =========================================================
# HOME
# =========================================================
@app.route("/")
def home():

    return f"{SERVER_ID} RUNNING"

# =========================================================
# CHECK REGISTER
# =========================================================
@app.route("/check-register", methods=["POST"])
def check_register():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        user_id = body.get("user_id")

        if not user_id:

            return jsonify({
                "registered": False
            })

        doc = worker_db.collection("user") \
            .document(user_id) \
            .get()

        if not doc.exists:

            return jsonify({
                "registered": False
            })

        data = doc.to_dict()

        return jsonify({

            "registered":
                data.get("register", False)
        })

    except Exception:

        traceback.print_exc()

        return jsonify({
            "registered": False
        })

# =========================================================
# REGISTER USER
# =========================================================
 

# =========================================================
# LINE HELPERS
# =========================================================
def reply_message(reply_token, text):

    try:

        requests.post(

            LINE_REPLY_API,

            headers=LINE_HEADERS,

            json={

                "replyToken":
                    reply_token,

                "messages": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            },

            timeout=10
        )

    except Exception as e:

        print("reply error:", e)
#=====================================
def push_message(user_id, text):

    try:

        requests.post(

            LINE_PUSH_API,

            headers=LINE_HEADERS,

            json={

                "to":
                    user_id,

                "messages": [
                    {
                        "type": "text",
                        "text": text
                    }
                ]
            },

            timeout=10
        )

    except Exception as e:

        print("push error:", e)
#---------------------------------------------------------
# =========================================================
# MAIN ROUTE
# =========================================================


# =========================================================
# MAIN ROUTE
# =========================================================
@app.route("/main-route", methods=["POST"])
def main_route():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        print("=" * 50)
        print("MAIN ROUTE")
        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))
        print("=" * 50)

        events = body.get("events", [])

 

        for event in events:

            if event.get("type") != "message":
                continue

            message = event.get(
                "message",
                {}
            )

            message_type = message.get(
                "type"
            )

            # ====================================
            # TEXT
            # ====================================
            if message_type == "text":

                text = message.get(
                    "text",
                    ""
                ).strip()

                parts = text.split(" ")

                command = parts[0].lower()

                if command == "imagecolor":

                    return imagecolor(
                        event,
                        parts
                    )

                else:

                    reply_message(

                        event.get(
                            "replyToken"
                        ),

                        "ไม่รู้จัก command"
                    )

            # ====================================
            # IMAGE
            # ====================================
            elif message_type == "image":

                return handle_image(
                     event
                     )

        return jsonify({
            "status": "success"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500

# =========================================================
# IMAGE COLOR
# imagecolor red
# =========================================================
def imagecolor(event, parts):

    try:

        reply_token = event.get(
            "replyToken"
        )

        source = event.get(
            "source",
            {}
        )

        user_id = source.get(
            "userId"
        )

        # ====================================
        # VALIDATE
        # ====================================

        if len(parts) < 2:

            reply_message(

                reply_token,

                "รูปแบบ:\n"
                "imagecolor red"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # GET LABEL
        # ====================================

        project_name = "imagecolor"

        label_name = parts[1].lower()

        # ====================================
        # SAVE SESSION
        # ====================================

        worker_db.collection(
            "dataset_session"
        ).document(user_id).set({

            "mode":
                "imagecolor",

            "project":
                project_name,

            "label":
                label_name,

            "updated_at":
                datetime.utcnow()
        })

        print("SESSION SAVED")

        # ====================================
        # REPLY
        # ====================================

        reply_message(

            reply_token,

            f"บันทึกเรียบร้อย\n\n"
            f"PROJECT: {project_name}\n"
            f"LABEL: {label_name}\n\n"
            f"ส่งรูปหมวด {label_name}"
        )

        return jsonify({
            "status": "success"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500

# =========================================================
# HANDLE IMAGE
# =========================================================
# HANDLE IMAGE
# =========================================================
def handle_image(event):

    try:

        reply_token = event.get(
            "replyToken"
        )

        source = event.get(
            "source",
            {}
        )

        user_id = source.get(
            "userId"
        )

        message = event.get(
            "message",
            {}
        )

        # ====================================
        # GET SESSION
        # ====================================

        session_doc = worker_db.collection(
            "dataset_session"
        ).document(user_id).get()

        if not session_doc.exists:

            reply_message(

                reply_token,

                "กรุณาพิมพ์:\n"
                "imagecolor red"
            )

            return jsonify({
                "status": "error"
            })

        session_data = session_doc.to_dict()

        project_name = session_data.get(
            "project"
        )

        label_name = session_data.get(
            "label"
        )

        print("PROJECT =", project_name)
        print("LABEL =", label_name)

        # ====================================
        # GET IMAGE FROM LINE
        # ====================================

        message_id = message.get("id")

        print("MESSAGE ID =", message_id)

        image_url = (
            "https://api-data.line.me/v2/bot/message/"
            f"{message_id}/content"
        )

        print("DOWNLOAD IMAGE FROM LINE")

        r = requests.get(

            image_url,

            headers={

                "Authorization":
                    f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
            },

            timeout=30
        )

        print("LINE STATUS =", r.status_code)

        if r.status_code != 200:

            print("LINE ERROR =", r.text)

            reply_message(

                reply_token,

                f"โหลดรูปไม่สำเร็จ\n"
                f"STATUS: {r.status_code}"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # OPEN IMAGE
        # ====================================

        image = Image.open(
            BytesIO(r.content)
        )

        # ====================================
        # RGB
        # ====================================

        image = image.convert(
            "RGB"
        )

        # ====================================
        # RESIZE
        # ====================================

        image = image.resize(
            (224, 224)
        )

        # ====================================
        # FILE NAME
        # ====================================

        filename = (
            str(uuid.uuid4())
            + ".jpg"
        )

        # ====================================
        # TEMP PATH
        # ====================================

        temp_path = (
            f"/tmp/{filename}"
        )

        image.save(

            temp_path,

            format="JPEG"
        )

        print(
            "IMAGE SAVED =",
            temp_path
        )

        # ====================================
        # STORAGE PATH
        # ====================================

        storage_path = (

            f"{project_name}/"
            f"{label_name}/"
            f"{filename}"
        )

        print(
            "STORAGE PATH =",
            storage_path
        )

        # ====================================
        # UPLOAD STORAGE
        # ====================================

        blob = bucket.blob(
            storage_path
        )

        blob.upload_from_filename(

            temp_path,

            content_type="image/jpeg"
        )

        blob.make_public()

        public_url = blob.public_url

        print("UPLOAD SUCCESS")
        print(public_url)

        # ====================================
        # SAVE FIRESTORE
        # ====================================

        worker_db.collection(
            project_name
        ).document(
            label_name
        ).collection(
            "dataset"
        ).add({

            "user_id":
                user_id,

            "project":
                project_name,

            "label":
                label_name,

            "storage_path":
                storage_path,

            "image_url":
                public_url,

            "width":
                224,

            "height":
                224,

            "created_at":
                datetime.utcnow()
        })

        print("DATASET SAVED")

        # ====================================
        # DELETE TEMP
        # ====================================

        if os.path.exists(
            temp_path
        ):

            os.remove(
                temp_path
            )

        # ====================================
        # REPLY
        # ====================================

        reply_message(

            reply_token,

            f"บันทึกรูปสำเร็จ\n\n"
            f"{label_name}\n"
            f"ส่งรูปต่อได้เลย"
        )

        return jsonify({
            "status": "success"
        })

    except Exception as e:

        traceback.print_exc()

        reply_message(

            event.get(
                "replyToken"
            ),

            f"ERROR\n{str(e)}"
        )

        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500
# =========================================================
 
 
# =========================================================
# RUN
# ======================================================
if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                8080
            )
        )
    )