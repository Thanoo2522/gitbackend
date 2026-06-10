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

        deviceId = body.get("deviceId")

        if not deviceId:

            return jsonify({
                "registered": False
            })

        doc = worker_db.collection("user") \
            .document(deviceId) \
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

        deviceId = body.get("deviceId")

        if not deviceId:

            return jsonify({

                "status": "error",

                "message": "no deviceId"

            }), 400

        worker_db.collection("user") \
            .document(deviceId) \
            .set({

                "deviceId":
                    deviceId,

                "fullname":
                    body.get("name", ""),

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

 #=====================================
 # =========================================================
# CREATE PROJECT  
# =========================================================
@app.route("/create_project", methods=["POST"])
def create_project():

    try:

        data = request.get_json()

        device_id = data.get("deviceId", "").strip()
        project_name = data.get("project", "").strip()
        class_name = data.get("className", "").strip()

        resize_width = int(
            data.get("resize_width", 224)
        )

        resize_height = int(
            data.get("resize_height", 224)
        )

        # -----------------------------
        # Validation
        # -----------------------------
        if not device_id:
            return jsonify({
                "success": False,
                "message": "deviceId missing"
            }), 400

        if not project_name:
            return jsonify({
                "success": False,
                "message": "project missing"
            }), 400

        if not class_name:
            return jsonify({
                "success": False,
                "message": "className missing"
            }), 400

        # -----------------------------
        # Storage Path
        # deviceId/project/class
        # -----------------------------
        folder_path = (
            f"{device_id}/"
            f"{project_name}/"
            f"{class_name}"
        )

        # -----------------------------
        # Count Images
        # Firebase Storage
        # -----------------------------
        total_images = 0

        blobs = bucket.list_blobs(
            prefix=folder_path + "/"
        )

        for blob in blobs:

            name = blob.name.lower()

            if (
                name.endswith(".jpg")
                or name.endswith(".jpeg")
                or name.endswith(".png")
                or name.endswith(".webp")
            ):
                total_images += 1

        # -----------------------------
        # Firestore
        # user/deviceId/
        # dataset_session/project/
        # class/className
        # -----------------------------
        doc_ref = (
            worker_db
            .collection("user")
            .document(device_id)
            .collection("dataset_session")
            .document(project_name)
            .collection("class")
            .document(class_name)
        )

        doc_ref.set({

            "label":
                class_name,

            "project":
                project_name,

            "resize_height":
                resize_height,

            "resize_width":
                resize_width,

            "total_images":
                total_images,

            "updated_at":
                firestore.SERVER_TIMESTAMP

        })

        return jsonify({

            "success": True,

            "message":
                "Project created",

            "deviceId":
                device_id,

            "project":
                project_name,

            "class":
                class_name

        })

    except Exception as ex:

        traceback.print_exc()

        return jsonify({

            "success": False,

            "message":
                str(ex)

        }), 500
#====================================================
@app.route("/get_projects", methods=["POST"])
def get_projects():

    try:

        data = request.get_json()
        device_id = data["deviceId"]

        result = []

        projects = (
            worker_db
            .collection("user")
            .document(device_id)
            .collection("dataset_session")
            .stream()
        )

        for project_doc in projects:

            project_data = project_doc.to_dict()

            project_name = project_data.get(
                "project",
                project_doc.id
            )

            classes = (
                worker_db
                .collection("user")
                .document(device_id)
                .collection("dataset_session")
                .document(project_doc.id)
                .collection("class")
                .stream()
            )

            for class_doc in classes:

                item = class_doc.to_dict()

                result.append({
                    "project": project_name,
                    "label": item.get("label", ""),
                    "resize_width": item.get("resize_width", 0),
                    "resize_height": item.get("resize_height", 0),
                    "total_images": item.get("total_images", 0)
                })

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as ex:

        return jsonify({
            "success": False,
            "message": str(ex)
        }), 500    
#=====================================================
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

            # =====================================================
            # TEXT MESSAGE
            # =====================================================
            if message_type == "text":
                text = message.get("text", "").strip()
                user_id = event["source"]["userId"]
                reply_token = event.get("replyToken")

                print("TEXT =", text)

                # =========================================
                # USER REF
                # =========================================
                user_ref = worker_db.collection("user").document(user_id)
                session_ref = user_ref.collection("dataset_session").document(user_id)

 
                # =========================================
                # DOWNLOAD
                # =========================================
                if text.lower().startswith("download"):
                    parts = text.split(" ")
                    return download_dataset(event, parts)

                # =========================================
 
   

            # =====================================================
            # IMAGE MESSAGE
            # =====================================================
            elif message_type == "image":
                return handle_image(event)

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
# =========================================================
def handle_image(event):
    try:
        reply_token = event.get("replyToken")
        source = event.get("source", {})
        user_id = source.get("userId")
        message = event.get("message", {})

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
 

        session_data = session_doc.to_dict()

        project_name = session_data.get("project")
        label_name = session_data.get("label")       # ค่าตัวอย่างเช่น "1"
        mode_name = session_data.get("mode", "universal") # ดึงเพิ่มเพื่อเอาไปใส่ใน Path ที่ 2

        resize_width = int(session_data.get("resize_width", 224))
        resize_height = int(session_data.get("resize_height", 224))

 
       # ====================================
        # RESIZE
        # ====================================
        image = image.resize((resize_width, resize_height))

        # ====================================
        # FILE NAME
        # ====================================
        filename = str(uuid.uuid4()) + ".jpg"
        temp_path = f"/tmp/{filename}"
        image.save(temp_path, format="JPEG")
        print("IMAGE SAVED =", temp_path)

        # ====================================
        # STORAGE PATH
        # ====================================
        storage_path = f"{user_id}/{project_name}/{label_name}/{filename}"
        print("STORAGE PATH =", storage_path)

        # ====================================
        # UPLOAD STORAGE
        # ====================================
        blob = bucket.blob(storage_path)
        blob.upload_from_filename(temp_path, content_type="image/jpeg")
        blob.make_public()
        public_url = blob.public_url
        print("UPLOAD SUCCESS:", public_url)

        # ====================================
        # COUNT IMAGES FROM STORAGE
        # ====================================
        storage_prefix = f"{user_id}/{project_name}/{label_name}/"
        blobs = list(bucket.list_blobs(prefix=storage_prefix))
        blobs = [b for b in blobs if not b.name.endswith("/")]
        total_images = len(blobs)
        print("TOTAL IMAGES =", total_images)

        # ====================================
        # WRITE / UPDATE TO NEW FIRESTORE PATHS
        # ====================================
        timestamp_now = firestore.SERVER_TIMESTAMP

        # ปลายทางที่ 1
        active_session_ref = worker_db.collection("user").document(user_id)\
                                      .collection("active_session").document(project_name)

        active_session_data = {
            "label": label_name,
            "resize_height": resize_height,
            "resize_width": resize_width,
            "total_images": total_images,
            "updated_at": timestamp_now
        }

        active_session_ref.set(active_session_data, merge=True)

        # ====================================
        # สร้าง document project ให้มีตัวตนจริง
        # ====================================
        dataset_project_ref = worker_db.collection("user")\
                                       .document(user_id)\
                                       .collection("dataset_session")\
                                       .document(project_name)

        dataset_project_ref.set({
            "project": project_name,
            "updated_at": timestamp_now
        }, merge=True)

        # ====================================
        # ปลายทางที่ 2
        # ====================================
        dataset_class_ref = worker_db.collection("user")\
                                     .document(user_id)\
                                     .collection("dataset_session")\
                                     .document(project_name)\
                                     .collection("class")\
                                     .document(label_name)

        dataset_class_data = {
            "label": label_name,
            "mode": mode_name,
            "project": project_name,
            "resize_height": resize_height,
            "resize_width": resize_width,
            "total_images": total_images,
            "updated_at": timestamp_now
        }

        dataset_class_ref.set(
            dataset_class_data,
            merge=True
        )

        # ====================================
        # DELETE TEMP
        # ====================================
        if os.path.exists(temp_path):
            os.remove(temp_path)

 

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()
 
        return jsonify({
            "status": "error",
            "message": str(e)
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
 
            # ====================================
            # FOLLOW EVENT
            # ====================================
 

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