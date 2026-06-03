#from click import command
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
#------------- เกี่ยวกับ AI Model
import numpy as np
from streamlit import user

 
 
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

LIFF_ID = os.environ.get("LIFF_ID")

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
    "LIFF_ID": LIFF_ID   
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
# REPLY FLEX MESSAGE
# =========================================================

def reply_flex(

    reply_token,

    alt_text,

    flex_contents
):

    url = "https://api.line.me/v2/bot/message/reply"

    headers = {

        "Content-Type":
            "application/json",

        "Authorization":
            f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    payload = {

        "replyToken":
            reply_token,

        "messages": [

            {

                "type":
                    "flex",

                "altText":
                    alt_text,

                "contents":
                    flex_contents
            }
        ]
    }

    r = requests.post(

        url,

        headers=headers,

        json=payload,

        timeout=30
    )

    print(
        "FLEX STATUS =",
        r.status_code
    )

    print(r.text)

# =========================================================
# REQUEST START
# =========================================================
@app.before_request
def before_request():

    global active_requests
    global total_requests
    global last_request_time

    active_requests += 1

    total_requests += 1

    last_request_time = int(time.time())

    g.start_time = time.time()
# =========================================================
# REQUEST END
# =========================================================
@app.after_request
def after_request(response):

    global active_requests

    active_requests -= 1

    if active_requests < 0:
        active_requests = 0

    process_time = time.time() - g.start_time

    print(
        f"REQUEST TIME = {process_time:.2f}s"
    )

    return response  
# =========================================================
# SERVER LOAD STATUS
# =========================================================
# ปกติ -> active_requests = 0-3
# เริ่มหนัก->active_requests = 5-10
# ขวดหนัก ->active_requests > 15
# =========================================================
# SERVER STATS
# =========================================================
from flask import g
active_requests = 0

total_requests = 0

last_request_time = 0

average_response_time = 0
# =========================================================
# HEARTBEAT LOOP
# =========================================================
def heartbeat_loop():

    global active_requests
    global total_requests
    global last_request_time
    global average_response_time

    print("🔥 HEARTBEAT LOOP STARTED")

    while True:

        try:

            # ====================================
            # HEALTH STATUS
            # ====================================

            if active_requests >= 20:

                health = "overload"

            elif active_requests >= 10:

                health = "busy"

            elif active_requests >= 5:

                health = "warning"

            else:

                health = "normal"

            # ====================================
            # LOAD SCORE
            # ====================================

            load_score = (

                active_requests * 10
            )

            # ====================================
            # SAVE DATA
            # ====================================

            save_data = {

                "server_id":
                    SERVER_ID,

                "status":
                    "online",

                # -----------------------------
                # LOAD
                # -----------------------------

                "load_score":
                    load_score,

                "health":
                    health,

                # -----------------------------
                # REQUEST STATS
                # -----------------------------

                "active_requests":
                    active_requests,

                "total_requests":
                    total_requests,

                "last_request_time":
                    last_request_time,

                "avg_response_time":
                    round(
                        average_response_time,
                        2
                    ),

                # -----------------------------
                # SERVER INFO
                # -----------------------------

                "cloud_url":
                    WORKER_WEBHOOK_URL,

                "last_heartbeat":
                    int(time.time())
            }

            # ====================================
            # SAVE FIRESTORE
            # ====================================

            hub_db.collection("hub_system") \
                .document("server_pool") \
                .collection("servers") \
                .document(SERVER_ID) \
                .set(save_data, merge=True)

            # ====================================
            # LOG
            # ====================================

            print("=" * 50)

            print("✅ HEARTBEAT OK")

            print("SERVER =", SERVER_ID)

            print("HEALTH =", health)

            print(
                "ACTIVE REQUESTS =",
                active_requests
            )

            print(
                "TOTAL REQUESTS =",
                total_requests
            )

            print(
                "AVG RESPONSE =",
                round(
                    average_response_time,
                    2
                ),
                "sec"
            )

            print(
                "LOAD SCORE =",
                load_score
            )

            print("=" * 50)

        except Exception:

            print("❌ HEARTBEAT ERROR")

            traceback.print_exc()

        # ====================================
        # LOOP EVERY 30 SEC
        # ====================================

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
@app.route("/register-user", methods=["POST"])
def register_user():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        print("REGISTER BODY =", body)

        user_id = body.get("user_id")

        if not user_id:

            return jsonify({

                "status": "error",

                "message": "no user_id"

            }), 400

        # ====================================
        # SAVE USER
        # ====================================

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

            "status": "success",

            "message": "ลงทะเบียนสำเร็จ"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status": "error",

            "message": str(e)

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

        # =====================================================
        # GET ACTIVE SESSION
        # =====================================================

        active_doc = worker_db.collection(
            "user"
        ).document(
            user_id
        ).collection(
            "active_session"
        ).document(
            "current"
        ).get()

        if not active_doc.exists:

            reply_message(

                reply_token,

                "กรุณาพิมพ์:\n"
                "project/class/224x224"
            )

            return jsonify({
                "status": "error"
            })

        active_data = active_doc.to_dict()

        project_name = active_data.get(
            "project"
        )

        class_name = active_data.get(
            "class"
        )

        # =====================================================
        # GET SESSION
        # =====================================================

        session_doc = worker_db.collection(
            "user"
        ).document(
            user_id
        ).collection(
            "dataset_session"
        ).document(
            project_name
        ).collection(
            "class"
        ).document(
            class_name
        ).get()

        if not session_doc.exists:

            reply_message(

                reply_token,

                "ไม่พบ session"
            )

            return jsonify({
                "status": "error"
            })

        session_data = session_doc.to_dict()

        resize_width = int(

            session_data.get(
                "resize_width",
                224
            )
        )

        resize_height = int(

            session_data.get(
                "resize_height",
                224
            )
        )

        print("PROJECT =", project_name)
        print("CLASS =", class_name)

        print(
            "SIZE =",
            resize_width,
            resize_height
        )

        # =====================================================
        # GET IMAGE FROM LINE
        # =====================================================

        message_id = message.get(
            "id"
        )

        image_url = (
            "https://api-data.line.me/v2/bot/message/"
            f"{message_id}/content"
        )

        print("DOWNLOAD IMAGE")

        r = requests.get(

            image_url,

            headers={

                "Authorization":
                    f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
            },

            timeout=30
        )

        print(
            "LINE STATUS =",
            r.status_code
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

        # =====================================================
        # OPEN IMAGE
        # =====================================================

        image = Image.open(
            BytesIO(r.content)
        )

        image = image.convert(
            "RGB"
        )

        # =====================================================
        # RESIZE
        # =====================================================

        image = image.resize(

            (
                resize_width,
                resize_height
            )
        )

        # =====================================================
        # FILE NAME
        # =====================================================

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

        print(
            "IMAGE SAVED =",
            temp_path
        )

        # =====================================================
        # STORAGE PATH
        # =====================================================

        storage_path = (

            f"{user_id}/"
            f"{project_name}/"
            f"{class_name}/"
            f"{filename}"
        )

        print(
            "STORAGE PATH =",
            storage_path
        )

        # =====================================================
        # UPLOAD STORAGE
        # =====================================================

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

        # =====================================================
        # COUNT IMAGES
        # =====================================================

        storage_prefix = (

            f"{user_id}/"
            f"{project_name}/"
            f"{class_name}/"
        )

        blobs = list(

            bucket.list_blobs(
                prefix=storage_prefix
            )
        )

        blobs = [

            b for b in blobs
            if not b.name.endswith("/")
        ]

        total_images = len(
            blobs
        )

        print(
            "TOTAL IMAGES =",
            total_images
        )

        # =====================================================
        # UPDATE SESSION MONITOR
        # =====================================================

        session_doc.reference.update({

            "total_images":
                total_images,

            "last_upload":
                datetime.utcnow(),

            "worker_online":
                True
        })

        # =====================================================
        # DELETE TEMP
        # =====================================================

        if os.path.exists(
            temp_path
        ):

            os.remove(
                temp_path
            )

        # =====================================================
        # REPLY
        # =====================================================

        reply_message(

            reply_token,

            f"บันทึกรูปสำเร็จ\n\n"
            f"PROJECT: {project_name}\n"
            f"CLASS: {class_name}\n"
            f"SIZE: "
            f"{resize_width}x{resize_height}\n"
            f"TOTAL: {total_images}\n\n"
            f"ส่งรูปต่อได้"
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
# FLEX PROJECT MONITOR
# =========================================================

def create_project_monitor_flex(projects_data):

    bubbles = []

    # =====================================================
    # LIMIT 40 BUBBLES
    # =====================================================

    projects_data = projects_data[:40]

    for item in projects_data:

        project_name = item.get(
            "project_name",
            "-"
        )

        total_classes = item.get(
            "total_classes",
            0
        )

        total_images = item.get(
            "total_images",
            0
        )

        latest_upload = item.get(
            "latest_upload",
            "-"
        )

        # =================================================
        # STATUS
        # =================================================

        if total_images >= 1000:

            status_text = "🟢 READY"

        elif total_images >= 100:

            status_text = "🟡 COLLECTING"

        else:

            status_text = "🔴 LOW DATA"

        # =================================================
        # BUBBLE
        # =================================================

        bubble = {

            "type": "bubble",

            "size": "mega",

            "body": {

                "type": "box",

                "layout": "vertical",

                "spacing": "md",

                "contents": [

                    {
                        "type": "text",

                        "text":
                            "🤖 AI PROJECT",

                        "size":
                            "sm",

                        "color":
                            "#999999"
                    },

                    {
                        "type": "text",

                        "text":
                            project_name,

                        "weight":
                            "bold",

                        "size":
                            "xl",

                        "wrap":
                            True
                    },

                    {
                        "type": "separator",

                        "margin":
                            "md"
                    },

                    {
                        "type": "box",

                        "layout": "vertical",

                        "margin":
                            "md",

                        "spacing":
                            "sm",

                        "contents": [

                            {
                                "type": "text",

                                "text":
                                    f"📦 CLASS: {total_classes}",

                                "size":
                                    "sm"
                            },

                            {
                                "type": "text",

                                "text":
                                    f"🖼️ IMAGES: {total_images}",

                                "size":
                                    "sm"
                            },

                            {
                                "type": "text",

                                "text":
                                    status_text,

                                "size":
                                    "sm",

                                "weight":
                                    "bold"
                            },

                            {
                                "type": "text",

                                "text":
                                    f"⏱ {latest_upload}",

                                "size":
                                    "xs",

                                "color":
                                    "#999999",

                                "wrap":
                                    True
                            }
                        ]
                    }
                ]
            },

            "footer": {

                "type": "box",

                "layout": "vertical",

                "spacing": "sm",

                "contents": [

                    {
                        "type": "button",

                        "style": "primary",

                        "height": "sm",

                        "action": {

                            "type": "message",

                            "label": "OPEN",

                            "text":
                                f"project {project_name}"
                        }
                    }
                ]
            }
        }

        bubbles.append(
            bubble
        )

    # =====================================================
    # FLEX CAROUSEL
    # =====================================================

    return {

        "type": "carousel",

        "contents": bubbles
    }

#==============================
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

            # =================================================
            # TEXT
            # =================================================

            if message_type == "text":

                text = message.get(
                    "text",
                    ""
                ).strip()

                user_id = event[
                    "source"
                ][
                    "userId"
                ]

                reply_token = event.get(
                    "replyToken"
                )

                print(
                    "TEXT =",
                    text
                )

                user_ref = (
                    worker_db
                    .collection("user")
                    .document(user_id)
                )

                # =================================================
                # PROJECT ALL
                # =================================================

                if text.lower() == "project all":

                    try:

                        dataset_ref = (
                            user_ref
                            .collection(
                                "dataset_session"
                            )
                        )

                        project_docs = (
                            dataset_ref.stream()
                        )

                        projects_data = []

                        total_projects = 0

                        for project_doc in project_docs:

                            total_projects += 1

                            project_name = (
                                project_doc.id
                            )

                            print(
                                f"PROJECT = {project_name}"
                            )

                            classes_ref = (
                                dataset_ref
                                .document(
                                    project_name
                                )
                                .collection(
                                    "class"
                                )
                                .stream()
                            )

                            total_classes = 0
                            total_images = 0

                            latest_upload = None

                            for class_doc in classes_ref:

                                total_classes += 1

                                class_data = (
                                    class_doc.to_dict()
                                    or {}
                                )

                                total_images += int(
                                    class_data.get(
                                        "total_images",
                                        0
                                    )
                                )

                                last_upload = (
                                    class_data.get(
                                        "last_upload"
                                    )
                                )

                                if last_upload:

                                    if latest_upload is None:

                                        latest_upload = (
                                            last_upload
                                        )

                                    elif (
                                        last_upload >
                                        latest_upload
                                    ):

                                        latest_upload = (
                                            last_upload
                                        )

                            if latest_upload:

                                latest_upload = str(
                                    latest_upload
                                )

                            else:

                                latest_upload = "-"

                            projects_data.append({

                                "project_name":
                                    project_name,

                                "total_classes":
                                    total_classes,

                                "total_images":
                                    total_images,

                                "latest_upload":
                                    latest_upload
                            })

                        print(
                            json.dumps(
                                projects_data,
                                indent=2,
                                ensure_ascii=False,
                                default=str
                            )
                        )

                        if total_projects == 0:

                            reply_message(

                                reply_token,

                                "ยังไม่มี Project"
                            )

                            return jsonify({
                                "status":
                                    "success"
                            })

                        flex_json = (
                            create_project_monitor_flex(
                                projects_data
                            )
                        )

                        reply_flex(

                            reply_token,

                            "AI PROJECT MONITOR",

                            flex_json
                        )

                        return jsonify({

                            "status":
                                "success"
                        })

                    except Exception as e:

                        traceback.print_exc()

                        reply_message(

                            reply_token,

                            f"เกิดข้อผิดพลาด\n{str(e)}"
                        )

                        return jsonify({

                            "status":
                                "error"
                        })

                # =====================================================
                # RESET
                # =====================================================

                if text.lower() == "reset":

                    active_ref = (
                        user_ref
                        .collection(
                            "active_session"
                        )
                        .document(
                            "current"
                        )
                    )

                    active_doc = active_ref.get()

                    if active_doc.exists:

                        active_ref.delete()

                    reply_message(
                        reply_token,
                        "ล้าง active session แล้ว"
                    )

                    return jsonify({
                        "status": "success"
                    })

                # =====================================================
                # SESSION
                # =====================================================

                if text.lower() == "session":

                    ...
                    # โค้ดเดิมของคุณ
                    ...

                # =====================================================
                # FORMAT
                # project/class/224x224
                # =====================================================

                path_parts = text.split("/")

                if len(path_parts) < 3:

                    reply_message(

                        reply_token,

                        "รูปแบบ:\n"
                        "project/class/224x224\n\n"
                        "ตัวอย่าง:\n"
                        "imagenumber/5/224x224\n"
                        "plant/rust/640x480\n\n"
                        "หรือใช้:\n"
                        "project all"
                    )

                    return jsonify({
                        "status": "error"
                    })

                # ส่วนที่เหลือใช้โค้ดเดิมของคุณต่อได้เลย

            # =================================================
            # IMAGE
            # =================================================

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

#=======================================   
def download_dataset(event, parts):

    try:

        reply_token = event.get(
            "replyToken"
        )

        user_id = event.get(
            "source",
            {}
        ).get(
            "userId"
        )

        # ====================================
        # VALIDATE
        # ====================================

        if len(parts) < 2:

            reply_message(

                reply_token,

                "รูปแบบ:\n"
                "download meter\n"
                "download meter water"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # PROJECT
        # ====================================

        project_name = parts[1].lower()

        # ====================================
        # FULL PROJECT
        # ====================================

        if len(parts) == 2:

            label_name = "ALL"

            storage_prefix = (

                f"{user_id}/"
                f"{project_name}/"
            )

        # ====================================
        # SINGLE LABEL
        # ====================================

        else:

            label_name = parts[2].lower()

            storage_prefix = (

                f"{user_id}/"
                f"{project_name}/"
                f"{label_name}/"
            )

        print("=" * 50)
        print("DOWNLOAD DATASET")
        print("USER =", user_id)
        print("PREFIX =", storage_prefix)
        print("=" * 50)

        # ====================================
        # GET FILES
        # ====================================

        blobs = list(

            bucket.list_blobs(
                prefix=storage_prefix
            )
        )

        # FILTER FOLDER

        blobs = [

            b for b in blobs
            if not b.name.endswith("/")
        ]

        print("TOTAL FILES =", len(blobs))

        # ====================================
        # EMPTY
        # ====================================

        if len(blobs) == 0:

            reply_message(

                reply_token,

                f"ไม่พบ dataset\n\n"
                f"{storage_prefix}"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # ZIP NAME
        # ====================================

        zip_filename = (

            f"{project_name}_"
            f"{label_name}.zip"
        )

        zip_temp_path = (
            f"/tmp/{zip_filename}"
        )

        # ====================================
        # CREATE ZIP
        # ====================================

        with zipfile.ZipFile(

            zip_temp_path,

            "w",

            zipfile.ZIP_DEFLATED

        ) as zipf:

            for blob in blobs:

                filename = os.path.basename(
                    blob.name
                )

                if not filename:
                    continue

                temp_file = (
                    f"/tmp/{filename}"
                )

                blob.download_to_filename(
                    temp_file
                )

                # IMPORTANT
                zipf.write(

                    temp_file,

                    arcname=blob.name.replace(
                        f"{user_id}/",
                        ""
                    )
                )

                if os.path.exists(
                    temp_file
                ):

                    os.remove(
                        temp_file
                    )

        # ====================================
        # UPLOAD ZIP
        # ====================================

        zip_storage_path = (

            f"{user_id}/downloads/"
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

        # ====================================
        # DELETE TEMP
        # ====================================

        if os.path.exists(
            zip_temp_path
        ):

            os.remove(
                zip_temp_path
            )

        # ====================================
        # REPLY
        # ====================================

        reply_message(

            reply_token,

            f"DOWNLOAD READY\n\n"

            f"PROJECT: {project_name}\n"

            f"MODE: {label_name}\n"

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

            f"DOWNLOAD ERROR\n\n{str(e)}"
        )

        return jsonify({

            "status":
                "error",

            "message":
                str(e)

        }), 500   

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

            # ====================================
            # GET USER
            # ====================================

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
            # MESSAGE EVENT
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

                # SAVE CHAT LOG
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

                # COMMANDS
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
            # FOLLOW EVENT
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

            "status": "error",

            "message": str(e)

        }), 500 
 
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