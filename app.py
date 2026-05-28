# from click import command
from flask import Flask, request, jsonify
from flask import render_template

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
import zipfile

import cv2
import numpy as np

# ================================================
# FLASK
# =========================================================
app = Flask(__name__)

# =========================================================
# MODELS
# =========================================================
models = {}

# =========================================================
# LABELS
# =========================================================
labels = {

    "imagenumber": [
        "1",
        "2"
    ]
}

print("LABELS LOADED")

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

LIFF_ID = os.environ.get(
    "LIFF_ID"
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
        WORKER_WEBHOOK_URL,

    "LIFF_ID":
        LIFF_ID
}

for k, v in required_env.items():

    if not v:
        raise RuntimeError(f"Missing {k}")

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

bucket = storage.bucket(
    app=worker_app
)



# =========================================================
# START HEARTBEAT LAZY
# =========================================================
@app.before_request
def before_first_request():

    global heartbeat_started

    if not heartbeat_started:

        start_heartbeat_once()
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
# HEARTBEAT LOOP
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

        except Exception:

            print("❌ HEARTBEAT ERROR")

            traceback.print_exc()

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

        user_id = body.get(
            "user_id"
        )

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
@app.route("/register-user", methods=["POST"])
def register_user():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        print("REGISTER BODY =", body)

        user_id = body.get(
            "user_id"
        )

        if not user_id:

            return jsonify({

                "status":
                    "error",

                "message":
                    "no user_id"

            }), 400

        worker_db.collection("user") \
            .document(user_id) \
            .set({

                "userId":
                    user_id,

                "fullname":
                    body.get("name", ""),

                "phone":
                    body.get("phone", ""),

                "email":
                    body.get("email", ""),

                "register":
                    True,

                "worker_id":
                    SERVER_ID,

                "created_at":
                    datetime.utcnow()
            })

        print("✅ USER SAVED")

        return jsonify({

            "status":
                "success",

            "message":
                "ลงทะเบียนสำเร็จ"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500

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

# =========================================================
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

# =========================================================
# GENERIC DATASET COMMAND
# FORMAT:
# imagenumber 5
# imagecolor red
# imagemater water
# =========================================================
def image_dataset_command(

    event,
    parts,
    project_name,
    validate_number=False
):

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

                f"รูปแบบ:\n"
                f"{project_name} label"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # LABEL
        # ====================================

        label_name = parts[1].strip().lower()

        # ====================================
        # NUMBER VALIDATION
        # ====================================

        if validate_number:

            if not label_name.isdigit():

                reply_message(

                    reply_token,

                    "label ต้องเป็นตัวเลข\n"
                    "เช่น:\n"
                    "imagenumber 5"
                )

                return jsonify({
                    "status": "error"
                })

        # ====================================
        # SAVE SESSION
        # ====================================

        worker_db.collection(
            "dataset_session"
        ).document(user_id).set({

            "mode":
                "dataset",

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

            f"บันทึกเรียบร้อย\n"
            f"PROJECT: {project_name}\n"
            f"CLASS: {label_name}\n\n"
            f"ส่งรูปได้เลย"
        )

        return jsonify({
            "status": "success"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500

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

        events = body.get(
            "events",
            []
        )

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

                # ====================================
                # DOWNLOAD
                # ====================================
                if command == "download":

                    return download_dataset(
                        event,
                        parts
                    )

                # ====================================
                # IMAGE NUMBER
                # ====================================
                elif command == "imagenumber":

                    return image_dataset_command(

                        event=event,

                        parts=parts,

                        project_name="imagenumber",

                        validate_number=True
                    )

                # ====================================
                # GENERIC FORMAT
                # imagemater water
                # imagecolor red
                # imagefruit banana
                # ====================================
                elif command.startswith("image"):

                    return image_dataset_command(

                        event=event,

                        parts=parts,

                        project_name=command,

                        validate_number=False
                    )

                # ====================================
                # UNKNOWN
                # ====================================
                else:

                    reply_message(

                        event.get(
                            "replyToken"
                        ),

                        "ไม่รู้จัก command"
                    )

            # ====================================
            # IMAGE MESSAGE
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

            "status":
                "error",

            "message":
                str(e)

        }), 500

# =========================================================
# DOWNLOAD DATASET
# =========================================================
def download_dataset(event, parts):

    try:

        reply_token = event.get(
            "replyToken"
        )

        if len(parts) < 2:

            reply_message(

                reply_token,

                "รูปแบบ:\n"
                "download imagenumber"
            )

            return jsonify({
                "status": "error"
            })

        project_name = parts[1].lower()

        print(
            "DOWNLOAD PROJECT =",
            project_name
        )

        storage_prefix = (
            f"{project_name}/"
        )

        blobs = list(

            bucket.list_blobs(
                prefix=storage_prefix
            )
        )

        blobs = [

            blob for blob in blobs

            if not blob.name.endswith("/")
        ]

        if len(blobs) == 0:

            reply_message(

                reply_token,

                f"ไม่พบ dataset\n"
                f"PROJECT: {project_name}"
            )

            return jsonify({
                "status": "error"
            })

        print(
            "TOTAL FILES =",
            len(blobs)
        )

        MAX_FILES = 3000

        if len(blobs) > MAX_FILES:

            reply_message(

                reply_token,

                f"ไฟล์เกิน {MAX_FILES}\n"
                f"กรุณาลดจำนวนรูป"
            )

            return jsonify({
                "status": "error"
            })

        zip_filename = (

            f"{project_name}_"
            f"{uuid.uuid4().hex}.zip"
        )

        zip_temp_path = (
            f"/tmp/{zip_filename}"
        )

        with zipfile.ZipFile(

            zip_temp_path,

            "w",

            zipfile.ZIP_DEFLATED

        ) as zipf:

            for blob in blobs:

                try:

                    if blob.name.endswith("/"):
                        continue

                    print(
                        "ADD ZIP =",
                        blob.name
                    )

                    file_bytes = (
                        blob.download_as_bytes()
                    )

                    arcname = blob.name.replace(
                        f"{project_name}/",
                        ""
                    )

                    zipf.writestr(

                        arcname,

                        file_bytes
                    )

                except Exception:

                    print(
                        "ZIP ERROR =",
                        blob.name
                    )

                    traceback.print_exc()

        zip_storage_path = (

            f"downloads/"
            f"{zip_filename}"
        )

        zip_blob = bucket.blob(
            zip_storage_path
        )

        zip_blob.upload_from_filename(

            zip_temp_path,

            content_type="application/zip"
        )

        zip_blob.make_public()

        zip_url = zip_blob.public_url

        if os.path.exists(
            zip_temp_path
        ):

            os.remove(
                zip_temp_path
            )

        reply_message(

            reply_token,

            f"DOWNLOAD READY\n\n"
            f"PROJECT: {project_name}\n"
            f"FILES: {len(blobs)}\n\n"
            f"{zip_url}"
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

            f"DOWNLOAD ERROR\n{str(e)}"
        )

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500

# =========================================================
# AUTO CROP IMAGE
# =========================================================
def auto_crop_image(pil_image):

    try:

        image_np = np.array(
            pil_image
        )

        image_cv = cv2.cvtColor(

            image_np,

            cv2.COLOR_RGB2BGR
        )

        gray = cv2.cvtColor(

            image_cv,

            cv2.COLOR_BGR2GRAY
        )

        blur = cv2.GaussianBlur(

            gray,

            (5, 5),

            0
        )

        _, thresh = cv2.threshold(

            blur,

            120,

            255,

            cv2.THRESH_BINARY_INV
        )

        contours, _ = cv2.findContours(

            thresh,

            cv2.RETR_EXTERNAL,

            cv2.CHAIN_APPROX_SIMPLE
        )

        if len(contours) == 0:

            print("NO CONTOUR")

            return pil_image

        biggest = max(

            contours,

            key=cv2.contourArea
        )

        x, y, w, h = cv2.boundingRect(
            biggest
        )

        padding = 20

        x = max(0, x - padding)
        y = max(0, y - padding)

        w = w + (padding * 2)
        h = h + (padding * 2)

        img_h, img_w = image_cv.shape[:2]

        if x + w > img_w:
            w = img_w - x

        if y + h > img_h:
            h = img_h - y

        crop = image_cv[
            y:y+h,
            x:x+w
        ]

        if crop.size == 0:

            return pil_image

        crop_rgb = cv2.cvtColor(

            crop,

            cv2.COLOR_BGR2RGB
        )

        return Image.fromarray(
            crop_rgb
        )

    except Exception:

        print("AUTO CROP ERROR")

        traceback.print_exc()

        return pil_image

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
        # SESSION
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

        image_url = (
            "https://api-data.line.me/v2/bot/message/"
            f"{message_id}/content"
        )

        r = requests.get(

            image_url,

            headers={

                "Authorization":
                    f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
            },

            timeout=30
        )

        if r.status_code != 200:

            reply_message(

                reply_token,

                f"โหลดรูปไม่สำเร็จ\n"
                f"STATUS: {r.status_code}"
            )

            return jsonify({
                "status": "error"
            })

        image = Image.open(
            BytesIO(r.content)
        )

        image = image.convert(
            "RGB"
        )

        image = auto_crop_image(
            image
        )

        image = image.resize(
            (224, 224)
        )

        filename = (
            str(uuid.uuid4())
            + ".jpg"
        )

        temp_path = (
            f"/tmp/{filename}"
        )

        image.save(

            temp_path,

            format="JPEG"
        )

        # ====================================
        # STORAGE FORMAT
        # project/label/file.jpg
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
        # UPLOAD
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

        # ====================================
        # GENERIC FIRESTORE FORMAT
        #
        # dataset
        #   project
        #       imagenumber
        #
        # subcollection:
        #   items
        # ====================================

        worker_db.collection(
            "dataset"
        ).document(
            project_name
        ).collection(
            "items"
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
        # COUNT
        # ====================================

        dataset_docs = worker_db.collection(
            "dataset"
        ).document(
            project_name
        ).collection(
            "items"
        ).where(
            "label",
            "==",
            label_name
        ).get()

        total_images = len(
            dataset_docs
        )

        print(
            "TOTAL IMAGES =",
            total_images
        )

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

            f"บันทึกรูปสำเร็จ\n"
            f"PROJECT: {project_name}\n"
            f"CLASS: {label_name}\n"
            f"จำนวนรูป: {total_images}\n\n"
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

            "status":
                "error",

            "message":
                str(e)

        }), 500

# =========================================================
# WORKER WEBHOOK
# =========================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        print("=" * 50)
        print("WORKER WEBHOOK")
        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))
        print("=" * 50)

        events = body.get("events", [])

        for event in events:

            event_type = event.get("type")

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

            if not user_id:
                continue

            user_doc = worker_db.collection("user") \
                .document(user_id) \
                .get()

            if not user_doc.exists:
                continue

            user_data = user_doc.to_dict()

            fullname = user_data.get(
                "fullname",
                "Unknown"
            )

            # ====================================
            # MESSAGE
            # ====================================

            if event_type == "message":

                text = event.get(
                    "message",
                    {}
                ).get(
                    "text",
                    ""
                )

                print("TEXT =", text)

                worker_db.collection("chat_logs") \
                    .add({

                        "user_id":
                            user_id,

                        "fullname":
                            fullname,

                        "text":
                            text,

                        "timestamp":
                            datetime.utcnow()
                    })

                if text.lower() == "ping":

                    reply_message(
                        reply_token,
                        "pong"
                    )

                elif text.lower() == "profile":

                    reply_message(

                        reply_token,

                        f"ชื่อ: {fullname}\n"
                        f"USER: {user_id}\n"
                        f"WORKER: {SERVER_ID}"
                    )

                else:

                    reply_message(

                        reply_token,

                        f"สวัสดี {fullname}\n\n"
                        f"คุณพิมพ์: {text}"
                    )

            # ====================================
            # FOLLOW
            # ====================================

            elif event_type == "follow":

                push_message(
                    user_id,
                    "ยินดีต้อนรับ"
                )

        return jsonify({
            "status": "success"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":

    print("RUN APP...")

    port = int(
        os.environ.get(
            "PORT",
            8080
        )
    )

    app.run(

        host="0.0.0.0",

        port=port,

        debug=False
    )