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
#=========================================
def reply_message(reply_token, payload):
    """
    ฟังก์ชันส่งข้อความกลับไปยัง LINE รองรับทั้งแบบ String ข้อความธรรมดา 
    และแบบ Dictionary (สำหรับ Flex Message)
    """
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    
    # ถ้า payload ส่งมาเป็นข้อความธรรมดา (String)
    if isinstance(payload, str):
        messages = [{
            "type": "text",
            "text": payload
        }]
    # ถ้า payload ส่งมาเป็นโครงสร้าง Flex Message (Dict)
    elif isinstance(payload, dict):
        messages = [payload]
    else:
        print("Invalid payload type")
        return

    data = {
        "replyToken": reply_token,
        "messages": messages
    }
    
    r = requests.post(url, headers=headers, data=json.dumps(data), timeout=30)
    print("REPLY STATUS =", r.status_code)
    return r
#=====================================================
# def build_project_all_flex(all_classes):

    bubbles = []

    for item in all_classes[:10]:

        command_text = (
            f"{item['project']}/"
            f"{item['label']}/"
            f"{item['resize_width']}x"
            f"{item['resize_height']}"
        )

        bubble = {
            "type": "bubble",
            "size": "mega",

            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",

                "contents": [

                    {
                        "type": "text",
                        "text": item["project"],
                        "weight": "bold",
                        "size": "lg"
                    },

                    {
                        "type": "text",
                        "text": f"Class : {item['label']}"
                    },

                    {
                        "type": "text",
                        "text": f"Mode : {item['mode']}"
                    },

                    {
                        "type": "text",
                        "text": (
                            f"Size : "
                            f"{item['resize_width']}x"
                            f"{item['resize_height']}"
                        )
                    },

                    {
                        "type": "text",
                        "text": (
                            f"Images : "
                            f"{item['total_images']}"
                        )
                    }
                ]
            },

            "footer": {
                "type": "box",
                "layout": "vertical",

                "contents": [
                    {
                        "type": "button",

                        "style": "primary",

                        "action": {
                            "type": "message",

                            "label": "Select",

                            "text": command_text
                        }
                    }
                ]
            }
        }

        bubbles.append(bubble)

    return {
        "type": "flex",

        "altText": "Dataset Classes",

        "contents": {
            "type": "carousel",

            "contents": bubbles
        }
    } 
#==============================================     
def build_project_all_flex_grouped(all_classes):

    projects = {}

    for item in all_classes:

        project_name = item["project"]

        if project_name not in projects:
            projects[project_name] = []

        projects[project_name].append(item)

    bubbles = []

    for project_name, classes in projects.items():

        chunks = [
            classes[i:i + 4]
            for i in range(
                0,
                len(classes),
                4
            )
        ]

        for chunk in chunks:

            class_contents = []

            for item in chunk:

                command_text = (
                    f"{item['project']}/"
                    f"{item['label']}/"
                    f"{item['resize_width']}x"
                    f"{item['resize_height']}"
                )

                class_contents.append({

                    "type": "box",

                    "layout": "vertical",

                    "margin": "md",

                    "spacing": "sm",

                    "contents": [

                        {
                            "type": "text",
                            "text": f"CLASS {item['label']}",
                            "weight": "bold",
                            "size": "sm"
                        },

                        {
                            "type": "text",
                            "text":
                                f"{item['resize_width']}x"
                                f"{item['resize_height']}",
                            "size": "xs"
                        },

                        {
                            "type": "text",
                            "text":
                                f"{item['total_images']} images",
                            "size": "xs"
                        },

                        {
                                "type": "button",

                                "style": "primary",

                                "color": "#00C300",

                                  "height": "sm",

                            "action": {
                                "type": "message",
                                "label": "Select",
                                "text": command_text
                            }
                        },

                        {
                            "type": "separator",
                            "color":"#FF0000",
                            "margin": "md"
                        }
                    ]
                })

            bubble = {

                "type": "bubble",

                "size": "mega",
                    "styles": {

                                "header": {
                                            "backgroundColor": "#FFFFFF"
                                          },

                                "body": {
                                            "backgroundColor": "#FFF5F5"
                                       }
                                },

                "header": {

                    "type": "box",

                    "layout": "vertical",

                    "contents": [

                        {
                            "type": "text",

                            "text": project_name,
                                  
                            "weight": "bold",

                            "size": "lg"
                        }
                    ]
                },

                "body": {

                    "type": "box",

                    "layout": "vertical",

                    "contents": class_contents
                }
            }

            bubbles.append(bubble)

    bubbles = bubbles[:10]

    return {
        "type": "flex",

        "altText": "Dataset Classes",

        "contents": {
            "type": "carousel",
            "contents": bubbles
        }
    }
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
                # COMMAND: "project all" (สร้าง Flex Carousel 4x10)
                # =========================================
                if text.strip().lower() == "project all":

                    all_classes = []

                    project_docs = user_ref.collection(
                        "dataset_session"
                    ).stream()

                    for p_doc in project_docs:

                        proj_name = p_doc.id

                        print("PROJECT =", proj_name)

                        class_docs = (
                            user_ref
                            .collection("dataset_session")
                            .document(proj_name)
                            .collection("class")
                            .stream()
                        )

                        for c_doc in class_docs:

                            c_data = c_doc.to_dict() or {}

                            all_classes.append({
                                "project": c_data.get(
                                    "project",
                                    proj_name
                                ),
                                "label": c_data.get(
                                    "label",
                                    c_doc.id
                                ),
                                "mode": c_data.get(
                                    "mode",
                                    "universal"
                                ),
                                "resize_width": c_data.get(
                                    "resize_width",
                                    224
                                ),
                                "resize_height": c_data.get(
                                    "resize_height",
                                    224
                                ),
                                "total_images": c_data.get(
                                    "total_images",
                                    0
                                )
                            })

                    print("TOTAL CLASSES =", len(all_classes))

                    flex_payload = build_project_all_flex_grouped(all_classes)

                    reply_message(reply_token, flex_payload)

                    return jsonify({"status": "success"})                     
                    
                # =========================================
                # DOWNLOAD
                # =========================================
                if text.lower().startswith("download"):
                    parts = text.split(" ")
                    return download_dataset(event, parts)

                # =========================================
                # RESET SESSION
                # =========================================
                if text.lower() == "reset":
                    session_ref.delete()
                    reply_message(reply_token, "ล้าง session แล้ว")
                    return jsonify({"status": "success"})

                # =========================================
                # SHOW SESSION
                # =========================================
                if text.lower() == "session":
                    session_doc = session_ref.get()
                    if not session_doc.exists:
                        reply_message(reply_token, "ไม่มี session")
                        return jsonify({"status": "error"})

                    data = session_doc.to_dict()
                    reply_message(
                        reply_token,
                        f"PROJECT: {data.get('project')}\n"
                        f"CLASS: {data.get('label')}\n"
                        f"SIZE: {data.get('resize_width')}x{data.get('resize_height')}"
                    )
                    return jsonify({"status": "success"})

                # =========================================
                # FORMAT: project/class/230x230
                # =========================================
                path_parts = text.split("/")
                if len(path_parts) < 3:
                    reply_message(
                        reply_token,
                        "รูปแบบ:\nproject/class/230x230\n\n"
                        "ตัวอย่าง:\nimagenumber/5/224x224\nplant/rust/640x480"
                    )
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
                except:
                    reply_message(reply_token, "ขนาดไม่ถูกต้อง\nเช่น 224x224")
                    return jsonify({"status": "error"})

                if resize_width <= 0 or resize_height <= 0:
                    reply_message(reply_token, "ขนาดต้องมากกว่า 0")
                    return jsonify({"status": "error"})

                # =========================================
                # SAVE SESSION
                # =========================================
                session_ref.set({
                    "project": project_name,
                    "label": class_name,
                    "resize_width": resize_width,
                    "resize_height": resize_height,
                    "mode": "universal",
                    "updated_at": datetime.utcnow()
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
                    f"SIZE: {resize_width}x{resize_height}\n\n"
                    f"ส่งรูปได้ต่อเนื่อง"
                )
                return jsonify({"status": "success"})

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

        if not session_doc.exists:
            reply_message(
                reply_token,
                "กรุณาพิมพ์:\n"
                "project/class/224x224"
            )
            return jsonify({"status": "error"})

        session_data = session_doc.to_dict()

        project_name = session_data.get("project")
        label_name = session_data.get("label")       # ค่าตัวอย่างเช่น "1"
        mode_name = session_data.get("mode", "universal") # ดึงเพิ่มเพื่อเอาไปใส่ใน Path ที่ 2

        resize_width = int(session_data.get("resize_width", 224))
        resize_height = int(session_data.get("resize_height", 224))

        print("PROJECT =", project_name)
        print("LABEL =", label_name)
        print("SIZE =", resize_width, resize_height)

        # ====================================
        # GET IMAGE FROM LINE
        # ====================================
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
            reply_message(
                reply_token,
                f"โหลดรูปไม่สำเร็จ\nSTATUS: {r.status_code}"
            )
            return jsonify({"status": "error"})

        # ====================================
        # OPEN IMAGE
        # ====================================
        image = Image.open(BytesIO(r.content))
        image = image.convert("RGB")

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

        # ====================================
        # REPLY
        # ====================================
        reply_message(
            reply_token,
            f"บันทึกรูปสำเร็จ\n\n"
            f"PROJECT: {project_name}\n"
            f"CLASS: {label_name}\n"
            f"SIZE: {resize_width}x{resize_height}\n"
            f"TOTAL: {total_images}\n\n"
            f"ส่งรูปต่อได้"
        )

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()
        reply_message(
            event.get("replyToken"),
            f"ERROR\n{str(e)}"
        )
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