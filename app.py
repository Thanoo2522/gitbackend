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
import tensorflow as tf


import pytz
# =========================================== 
# FLASK
# =========================================================
app = Flask(__name__)

# =========================================================
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
# LOAD MODEL (LAZY LOAD)
# =========================================================

def get_model(project):

    # ====================================
    # MODEL ALREADY LOADED
    # ====================================

    if project in models:

        print(f"{project} model already loaded")

        return models[project]

    # ====================================
    # LOAD NEW MODEL
    # ====================================

    print("=" * 50)
    print(f"LOADING MODEL : {project}")
    print("=" * 50)

    model_path = f"models/{project}.h5"

    print("MODEL PATH =", model_path)

    # ====================================
    # CHECK FILE
    # ====================================

    if not os.path.exists(model_path):

        raise Exception(
            f"Model file not found : {model_path}"
        )

    # ====================================
    # LOAD MODEL
    # ====================================

    model = tf.keras.models.load_model(
        model_path
    )

    models[project] = model

    print(f"{project} model loaded success")

    return model


# =========================================================
# PREDICT
# =========================================================
@app.route("/predict", methods=["POST"])
def predict():

    try:

        # ====================================
        # CHECK IMAGE
        # ====================================

        if "image" not in request.files:

            return jsonify({

                "status":
                    "error",

                "message":
                    "No image uploaded"

            }), 400

        # ====================================
        # GET FORM DATA
        # ====================================

        file = request.files["image"]

        project = request.form.get(
            "project"
        )

        # ====================================
        # DYNAMIC SIZE
        # ====================================

        width = int(
            request.form.get(
                "width",
                224
            )
        )

        height = int(
            request.form.get(
                "height",
                224
            )
        )

        print("=" * 50)
        print("PREDICT REQUEST")
        print("=" * 50)

        print("PROJECT =", project)
        print("WIDTH =", width)
        print("HEIGHT =", height)

        # ====================================
        # VALIDATE
        # ====================================

        if not project:

            return jsonify({

                "status":
                    "error",

                "message":
                    "Project is required"

            }), 400

        if project not in labels:

            return jsonify({

                "status":
                    "error",

                "message":
                    f"Unknown project : {project}"

            }), 400

        # ====================================
        # LOAD MODEL
        # ====================================

        model = get_model(
            project
        )

        class_names = labels[
            project
        ]

        # ====================================
        # OPEN IMAGE
        # ====================================

        image = Image.open(
            file.stream
        )

        image = image.convert(
            "RGB"
        )

        # ====================================
        # RESIZE
        # ====================================

        image = image.resize(
            (
                width,
                height
            )
        )

        # ====================================
        # NUMPY
        # ====================================

        img_array = np.array(
            image
        )

        img_array = img_array / 255.0

        img_array = np.expand_dims(

            img_array,

            axis=0
        )

        print("IMAGE READY")

        # ====================================
        # PREDICT
        # ====================================

        prediction = model.predict(
            img_array
        )

        index = np.argmax(
            prediction
        )

        confidence = float(
            prediction[0][index]
        )

        label = class_names[
            index
        ]

        print(
            "RESULT =",
            label,
            confidence
        )

        # ====================================
        # RESPONSE
        # ====================================

        return jsonify({

            "status":
                "success",

            "label":
                label,

            "confidence":
                confidence,

            "width":
                width,

            "height":
                height
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

            # =====================================================
            # TEXT MESSAGE
            # =====================================================
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

                print("TEXT =", text)

                # =========================================
                # USER REF
                # =========================================

                user_ref = worker_db.collection(
                    "user"
                ).document(
                    user_id
                )

                session_ref = user_ref.collection(
                    "dataset_session"
                ).document(
                    user_id
                )

                # =========================================
                # DOWNLOAD
                # =========================================

                if text.lower().startswith(
                    "download"
                ):

                    parts = text.split(" ")

                    return download_dataset(
                        event,
                        parts
                    )

                # =========================================
                # RESET SESSION
                # =========================================

                if text.lower() == "reset":

                    session_ref.delete()

                    reply_message(

                        reply_token,

                        "ล้าง session แล้ว"
                    )

                    return jsonify({
                        "status": "success"
                    })

                # =========================================
                # SHOW SESSION
                # =================================== 

                if text.lower() == "session":

                    session_doc = session_ref.get()

                    if not session_doc.exists:

                        reply_message(
                            reply_token,
                            "ไม่มี session"
                        )

                        return jsonify({
                            "status": "error"
                        })

                    data = session_doc.to_dict()

                    reply_message(

                        reply_token,

                        f"PROJECT: {data.get('project')}\n"
                        f"CLASS: {data.get('label')}\n"
                        f"SIZE: "
                        f"{data.get('resize_width')}x"
                        f"{data.get('resize_height')}"
                    )

                    return jsonify({
                        "status": "success"
                    })

                # =========================================
                # FORMAT:
                # project/class/230x230
                # =========================================

                path_parts = text.split("/")

                if len(path_parts) < 3:

                    reply_message(

                        reply_token,

                        "รูปแบบ:\n"
                        "project/class/230x230\n\n"
                        "ตัวอย่าง:\n"
                        "imagenumber/5/224x224\n"
                        "plant/rust/640x480"
                    )

                    return jsonify({
                        "status": "error"
                    })

                # =========================================
                # PROJECT
                # =========================================

                project_name = path_parts[0] \
                    .strip() \
                    .lower()

                # =========================================
                # CLASS
                # =============================== 

                class_name = path_parts[1] \
                    .strip() \
                    .lower()

                # =========================================
                # SIZE
                # =========================================

                size_text = path_parts[2] \
                    .strip() \
                    .lower()

                if "x" not in size_text:

                    reply_message(

                        reply_token,

                        "ขนาดผิดรูปแบบ\n"
                        "เช่น 224x224"
                    )

                    return jsonify({
                        "status": "error"
                    })

                try:

                    w, h = size_text.split("x")

                    resize_width = int(w)

                    resize_height = int(h)

                except:

                    reply_message(

                        reply_token,

                        "ขนาดไม่ถูกต้อง\n"
                        "เช่น 224x224"
                    )

                    return jsonify({
                        "status": "error"
                    })

                # =========================================
                # VALIDATE SIZE
                # =========================================

                if resize_width <= 0 \
                or resize_height <= 0:

                    reply_message(

                        reply_token,

                        "ขนาดต้องมากกว่า 0"
                    )

                    return jsonify({
                        "status": "error"
                    })

                # =========================================
                # SAVE SESSION
                # =========================================

                session_ref.set({

                    "project":
                        project_name,

                    "label":
                        class_name,

                    "resize_width":
                        resize_width,

                    "resize_height":
                        resize_height,

                    "mode":
                        "universal",

                    "updated_at":
                        datetime.utcnow()
                })

                print("SESSION SAVED")

                # =========================================
                # REPLY
                # =========================================

                reply_message(

                    reply_token,

                    f"📦 DATASET READY\n\n"
                    f"PROJECT: {project_name}\n"
                    f"CLASS: {class_name}\n"
                    f"SIZE: "
                    f"{resize_width}x{resize_height}\n\n"
                    f"ส่งรูปได้ต่อเนื่อง"
                )

                return jsonify({
                    "status": "success"
                })

            # =====================================================
            # IMAGE MESSAGE
            # =====================================================
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

            f"บันทึกเรียบร้อย\n"
            f"PROJECT: {project_name}\n"
            f"class: {label_name}\n\n"
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
# IMAGE NUMBER
# imagenumber  
# =========================================================
def imagenumber(event, parts):

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
                "imagenumber 5"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # GET LABEL
        # ====================================

        label_name = parts[1].strip()

        # ====================================
        # CHECK NUMBER
        # ====================================

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
        # PROJECT
        # ====================================

        project_name = "imagenumber"

        # ====================================
        # SAVE SESSION
        # ====================================

        worker_db.collection(
            "dataset_session"
        ).document(user_id).set({

            "mode":
                "imagenumber",

            "project":
                project_name,

            "label":
                label_name,

            "updated_at":
                datetime.utcnow()
        })

        print("NUMBER SESSION SAVED")

        # ====================================
        # REPLY
        # ====================================

        reply_message(

            reply_token,

            f"บันทึกเรียบร้อย\n"
            f"PROJECT: {project_name}\n"
            f"class: {label_name}\n\n"
            f"ส่งรูปเลข {label_name}"
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
# DOWNLOAD DATASET
# =========================================================
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

                "รูปแบบ:\n\n"
                "download imagenumber\n"
                "download imagenumber 1\n"
                "download plant rust"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # REMOVE download
        # ====================================

        path_parts = parts[1:]

        # ====================================
        # STORAGE PREFIX
        # ====================================

        storage_prefix = (

            f"{user_id}/"
            + "/".join(path_parts)
            + "/"
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

                "ไม่พบ dataset\n\n"
                f"{storage_prefix}"
            )

            return jsonify({
                "status": "error"
            })

        # ====================================
        # SAFE ZIP NAME
        # ====================================

        safe_name = "_".join(
            path_parts
        )

        # ====================================
        # THAI TIMESTAMP
        # ====================================

        thai_tz = pytz.timezone(
            "Asia/Bangkok"
        )

        thai_now = datetime.now(
            thai_tz
        )

        timestamp = thai_now.strftime(
            "%Y%m%d_%H%M%S"
        )

        # ====================================
        # ZIP FILE NAME
        # ====================================

        zip_filename = (

            f"{safe_name}_"
            f"{timestamp}.zip"
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

                try:

                    filename = os.path.basename(
                        blob.name
                    )

                    if not filename:
                        continue

                    temp_file = (
                        f"/tmp/{filename}"
                    )

                    # DOWNLOAD
                    blob.download_to_filename(
                        temp_file
                    )

                    # KEEP STRUCTURE
                    zipf.write(

                        temp_file,

                        arcname=blob.name.replace(
                            f"{user_id}/",
                            ""
                        )
                    )

                    # DELETE TEMP
                    if os.path.exists(
                        temp_file
                    ):

                        os.remove(
                            temp_file
                        )

                except Exception:

                    traceback.print_exc()

        # ====================================
        # STORAGE PATH
        # ====================================

        zip_storage_path = (

            f"{user_id}/"
            f"downloads/"
            f"{zip_filename}"
        )

        print(
            "ZIP STORAGE =",
            zip_storage_path
        )

        # ====================================
        # UPLOAD ZIP
        # ====================================

        zip_blob = bucket.blob(
            zip_storage_path
        )

        zip_blob.upload_from_filename(

            zip_temp_path,

            content_type="application/zip"
        )

        # IMPORTANT
        zip_blob.make_public()

        zip_url = zip_blob.public_url

        print("ZIP URL =", zip_url)

        # ====================================
        # DELETE TEMP ZIP
        # ====================================

        if os.path.exists(
            zip_temp_path
        ):

            os.remove(
                zip_temp_path
            )

        # ====================================
        # COUNT DOWNLOADS
        # ====================================

        download_blobs = list(

            bucket.list_blobs(

                prefix=(
                    f"{user_id}/downloads/"
                )
            )
        )

        download_blobs = [

            b for b in download_blobs
            if not b.name.endswith("/")
        ]

        total_downloads = len(
            download_blobs
        )

        print(
            "TOTAL DOWNLOADS =",
            total_downloads
        )

        # ====================================
        # REPLY
        # ====================================

        reply_message(

            reply_token,

            f"DOWNLOAD READY\n\n"

            f"PATH:\n"
            f"{storage_prefix}\n\n"

            f"FILES: {len(blobs)}\n"

            f"DOWNLOAD COUNT: "
            f"{total_downloads}\n\n"

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
            "user"
        ).document(
            user_id
        ).collection(
            "dataset_session"
        ).document(
            user_id
        ).get()

        if not session_doc.exists:

            reply_message(

                reply_token,

                "กรุณาพิมพ์:\n"
                "project/class/224x224"
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
        print("LABEL =", label_name)

        print(
            "SIZE =",
            resize_width,
            resize_height
        )

        # ====================================
        # GET IMAGE FROM LINE
        # ====================================

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

        # ====================================
        # OPEN IMAGE
        # ====================================

        image = Image.open(
            BytesIO(r.content)
        )

        image = image.convert(
            "RGB"
        )

        # ====================================
        # RESIZE
        # ====================================

        image = image.resize(

            (
                resize_width,
                resize_height
            )
        )

        # ====================================
        # FILE NAME
        # ====================================

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

        # ====================================
        # STORAGE PATH
        # ====================================

        storage_path = (

            f"{user_id}/"
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
        # COUNT IMAGES FROM STORAGE
        # ====================================

        storage_prefix = (

            f"{user_id}/"
            f"{project_name}/"
            f"{label_name}/"
        )

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

        total_images = len(
            blobs
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

            f"บันทึกรูปสำเร็จ\n\n"
            f"PROJECT: {project_name}\n"
            f"CLASS: {label_name}\n"
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