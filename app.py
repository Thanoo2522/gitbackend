#from click import command
from flask import Flask, request, jsonify , send_file
from flask import render_template


 
from flask import send_file

import os
import json
import traceback
import requests
import time
import threading

from datetime import datetime
from functools import wraps

import firebase_admin
from firebase_admin import credentials, firestore, storage
from PIL import (
    Image,
    ImageEnhance,
    ImageChops,
    ImageOps
)

 
from io import BytesIO
import uuid
import base64
import zipfile
#------------- เกี่ยวกับ AI Model
import numpy as np

from datetime import timedelta
from flask_cors import CORS

import tensorflow as tf
import io

 
 
import tempfile
import shutil
import threading
import traceback

 
 
import math
 
 

 

 

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)
CORS(
    app,
    resources={
        r"/*": {
            "origins": "*"
        }
    }
)
# =========================================================
# HEARTBEAT STATE
# =========================================================
heartbeat_started = False

# ==================================================
# Load Model
# ==================================================
BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

MODEL_PATH = os.path.join(
    BASE_DIR,
    "model.tflite"
)

interpreter = tf.lite.Interpreter(
    model_path=MODEL_PATH
)

interpreter.allocate_tensors()

input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
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

# ---------------------------------------------------------
# ADMIN SECRET KEY
# ใช้เช็ค header "X-Admin-Key" สำหรับ endpoint จัดการแผน/โควต้า
# ---------------------------------------------------------
ADMIN_SECRET_KEY = os.environ.get(
    "ADMIN_SECRET_KEY"
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
        WORKER_WEBHOOK_URL,

    "ADMIN_SECRET_KEY":
        ADMIN_SECRET_KEY

}

for k, v in required_env.items():

    if not v:
        raise RuntimeError(f"Missing {k}")

# หลังจากผ่าน loop ข้างบนแล้ว ตัวแปรเหล่านี้ไม่มีทาง None
# แต่ Pylance มองไม่เห็นความสัมพันธ์นั้น เลย assert ให้ชัดเจน
assert HUB_FIREBASE_KEY is not None
assert WORKER_FIREBASE_KEY is not None
assert SERVER_ID is not None
assert WORKER_WEBHOOK_URL is not None
assert ADMIN_SECRET_KEY is not None

# ==================================================
# FIREBASE

# ---------------------------------------------------------
# HUB DB
# -------------------------------------------
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
# ADMIN AUTH DECORATOR
# ตรวจ header "X-Admin-Key" ให้ตรงกับ ADMIN_SECRET_KEY
# ใช้กับ endpoint ที่เกี่ยวกับการจัดการแผน/โควต้า (admin only)
# =========================================================
def require_admin_key(f):

    @wraps(f)
    def wrapper(*args, **kwargs):

        client_key = request.headers.get("X-Admin-Key", "")

        if not client_key or client_key != ADMIN_SECRET_KEY:

            return jsonify({
                "status": "error",
                "message": "unauthorized"
            }), 401

        return f(*args, **kwargs)

    return wrapper

# ============================================
@app.route("/predict", methods=["POST"])
def predict():

    try:

        data = request.get_json(silent=True) or {}

        image_b64 = data.get("image")

        if not image_b64:
            return jsonify({
                "success": False,
                "error": "missing image"
            }), 400

        image_bytes = base64.b64decode(
            image_b64
        )

        image = Image.open(
            BytesIO(image_bytes)
        ).convert("RGB")

        image = image.resize(
            (224, 224)
        )

        # --------------------
        # FLOAT32 MODEL
        # --------------------

        img = np.array(
            image,
            dtype=np.float32
        )

        img = img / 255.0

        img = np.expand_dims(
            img,
            axis=0
        )

        interpreter.set_tensor(
            input_details[0]["index"],
            img
        )

        interpreter.invoke()

        output = interpreter.get_tensor(
            output_details[0]["index"]
        )

        score0 = float(output[0][0])
        score1 = float(output[0][1])
        score2 = float(output[0][2])

        # --------------------
        # LABELS
        # --------------------

        labels = [
            "class0",
            "class1",
            "class2"
        ]

        scores = [
            score0,
            score1,
            score2
        ]

        best_index = int(
            np.argmax(scores)
        )

        label = labels[
            best_index
        ]

        confidence = (
            scores[best_index]
            * 100.0
        )

        return jsonify({

            "success": True,

            "label": label,

            "confidence":
                round(
                    confidence,
                    1
                ),

            "scores": {
                "score0":
                    round(
                        score0,
                        4
                    ),

                "score1":
                    round(
                        score1,
                        4
                    ),

                "score2":
                    round(
                        score2,
                        4
                    )
            }

        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "success": False,

            "error": str(e)

        }), 500
# =========================================================
# HEARTBEAT LOOP กระตุ้กไปที่ HUB  ให้รู้ว่ายังonline อยู่
# ====================================================
def heartbeat_loop():

    print("🔥 HEARTBEAT LOOP STARTED")

    while True:

        try:

            # -------------------------
            # USER COUNT
            # -------------------------
            user_count = 0

            try:

                users = worker_db \
                    .collection("user") \
                    .stream()

                user_count = sum(
                    1 for _ in users
                )

            except Exception:
                traceback.print_exc()

            # -------------------------
            # LOAD SCORE
            # -------------------------
            load_score = user_count

            # -------------------------
            # SAVE HEARTBEAT
            # -------------------- 
            save_data = {

                "server_id":
                    SERVER_ID,

                "status":
                    "online",

                "active_users":
                    user_count,

                "load_score":
                    load_score,

                "cloud_url":
                    WORKER_WEBHOOK_URL,

                "last_heartbeat":
                    int(time.time())
            }

            hub_db.collection("hub_system") \
                .document("server_pool") \
                .collection("servers") \
                .document(SERVER_ID) \
                .set(
                    save_data,
                    merge=True
                )

            print(
                f"✅ HEARTBEAT OK "
                f"users={user_count} "
                f"load={load_score}"
            )

        except Exception:

            print(
                "❌ HEARTBEAT ERROR"
            )

            traceback.print_exc()

        # EVERY 30 SEC
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
# GET USER PLAN
# อ่านข้อมูลแผนปัจจุบันของ user จาก Firestore
# path: user/{email}/plan/select
# =========================================================
@app.route("/get_user_plan", methods=["POST"])
def get_user_plan():

    try:

        data = request.get_json(silent=True) or {}

        email = (
            data.get("email", "")
            .lower()
            .strip()
        )

        if not email:
            return jsonify({
                "success": False,
                "message": "no email"
            }), 400

        plan_doc = (
            worker_db
            .collection("user")
            .document(email)
            .collection("plan")
            .document("select")
            .get()
        )

        if not plan_doc.exists:
            return jsonify({
                "success": True,
                "plan": "Free",
                "usage": {},
                "limits": {}
            })

        plan_data = plan_doc.to_dict() or {}

        return jsonify({
            "success": True,
            "plan": plan_data.get("plan", "Free"),
            "status": plan_data.get("status", ""),
            "expireAt": str(plan_data.get("expireAt", "")),
            "usage": plan_data.get("usage", {}),
            "limits": plan_data.get("limits", {})
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500    

# =========================================================
# CHECK REGISTER
# =========================================================
@app.route("/check-register", methods=["POST", "OPTIONS"]) # 👈 1. เพิ่ม OPTIONS ตรงนี้
def check_register():
    # 🌟 2. ดักจับ Preflight Request ของเบราว์เซอร์
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    try:
        body = request.get_json(silent=True) or {}
        email = body.get("email")

        if not email:
            resp = jsonify({"registered": False})
            resp.headers.add("Access-Control-Allow-Origin", "*") # 👈 แนบ CORS Header
            return resp, 200

        doc = worker_db.collection("user").document(email).get()

        if not doc.exists:
            resp = jsonify({"registered": False})
            resp.headers.add("Access-Control-Allow-Origin", "*") # 👈 แนบ CORS Header
            return resp, 200

        data = doc.to_dict() or {}

        resp = jsonify({
            "registered": data.get("register", False)
        })
        resp.headers.add("Access-Control-Allow-Origin", "*") # 👈 แนบ CORS Header
        return resp, 200

    except Exception:
        traceback.print_exc()
        resp = jsonify({"registered": False})
        resp.headers.add("Access-Control-Allow-Origin", "*") # 👈 แนบ CORS Header
        return resp, 200  # หรือส่ง 500 ตามโครงสร้างเดิม
# ================================================= 
# REGISTER USER
# =============================================== 
from google.cloud import firestore  # ใช้ firestore.SERVER_TIMESTAMP

from datetime import datetime, timedelta

# ตัวอย่าง Starter อายุ 30 วัน
expire_at = datetime.utcnow() + timedelta(days=30)
@app.route("/register-user", methods=["POST"])
def register_user():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        print(
            "REGISTER BODY =",
            body
        )

        name = body.get("name", "")
        email = body.get("email", "").lower().strip()
        password = body.get("password", "")

        if not email:

            return jsonify({
                "status": "error",
                "message": "no email"
            }), 400

        if not password:

            return jsonify({
                "status": "error",
                "message": "no password"
            }), 400

        # เช็คซ้ำ
        user_ref = (
            worker_db
            .collection("user")
            .document(email)
        )

        if user_ref.get().exists:

            return jsonify({
                "status": "error",
                "message": "email already exists"
            }), 400

        # ------------------------------------------------
        # บันทึก user หลัก
        # ------------------------------------------------
        user_ref.set({

            "fullname": name,

            "email": email,

            "password": password,

            "register": True,

            "worker_id": SERVER_ID,

            "created_at":
                datetime.utcnow()

        })

        print("✅ USER SAVED")

        # ------------------------------------------------
        # บันทึก plan เริ่มต้น (Free) ที่ user/{email}/plan/select
        # ------------------------------------------------
        plan_ref = (
            user_ref
            .collection("plan")
            .document("select")
        )
        plan_ref.set({

    "email": email,

    "plan": "Free",

    "status": "active",

    "paymentStatus": "paid",

    "created_at":
        firestore.SERVER_TIMESTAMP,

    "paidAt":
        firestore.SERVER_TIMESTAMP,

    "expireAt":
        expire_at,

    "limits": {

        "maxProjects":1,

        "maxImages": 500,

        "storageBytes": 524288000   # 500 MB

    },

    "usage": {

        "totalImages": 0,

        "totalStorageBytes": 0,

        "lastUpdated":
            firestore.SERVER_TIMESTAMP

    }

})

        print("✅ PLAN SAVED (free)")

        return jsonify({

            "status": "success",

            "message":
                "ลงทะเบียนสำเร็จ"

        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500

 # login user
@app.route("/login-user", methods=["POST"])
def login_user():

    try:

        body = request.get_json(silent=True) or {}

        email = (
            body.get("email", "")
            .lower()
            .strip()
        )

        password = body.get(
            "password", ""
        )

        if not email:
            return jsonify({
                "status": "error",
                "message": "no email"
            }), 400

        doc = (
            worker_db
            .collection("user")
            .document(email)
            .get()
        )

        if not doc.exists:

            return jsonify({
                "status": "error",
                "message": "user not found"
            }), 404

        user = doc.to_dict() or {}

        if (
            user.get("password")
            != password
        ):

            return jsonify({
                "status": "error",
                "message": "invalid password"
            }), 401

        return jsonify({

            "status": "success",

            "email":
                user.get("email"),

            "fullname":
                user.get("fullname"),

            "worker_id":
                user.get("worker_id")

        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500

 #=====================
@app.route("/admin/login", methods=["POST"])
def admin_login():

    body = request.get_json(silent=True) or {}

    admin_key = body.get("adminKey", "").strip()

    if admin_key != ADMIN_SECRET_KEY:
        return jsonify({
            "status": "error",
            "message": "Invalid Admin Key"
        }), 401

    return jsonify({
        "status": "success"
    })  

# =========================================================
# GET PROJECTS (ใช้โดย Project.jsx - loadProjects())
# 3 route นี้ต้องมีอยู่เสมอ ไม่งั้น Project.jsx จะโหลดข้อมูลไม่ได้เลย
# ทั้ง 3 แท็บ (Classification / Detection / Segmentation)
# =========================================================
@app.route("/get_classification_projects", methods=["POST", "OPTIONS"])
def get_classification_projects():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    try:
        body = request.get_json(silent=True) or {}
        email = body.get("email")
        if not email:
            return jsonify({"success": False, "message": "Missing email"}), 400

        class_projects = {}

        # ดึงผ่าน collection_group เพื่อความแม่นยำและทะลุผ่าน Virtual Document
        class_groups = worker_db.collection_group("class").stream()

        for doc in class_groups:
            path_str = doc.reference.path
            # กรองเฉพาะของอีเมลนี้ และต้องไม่อยู่ในกลุ่ม detection/segmentation
            if f"user/{email}/dataset_session" in path_str and "detection" not in path_str and "segmentation" not in path_str:
                parts = path_str.split("/")
                if "dataset_session" in parts:
                    idx = parts.index("dataset_session")
                    project_name = parts[idx + 1]

                    cls_data = doc.to_dict()
                    img_count = cls_data.get("total_images", 0)
                    r_width = cls_data.get("resize_width", 224)
                    r_height = cls_data.get("resize_height", 224)

                    if project_name not in class_projects:
                        class_projects[project_name] = {
                            "project": project_name,
                            "project_type": "classification",
                            "resize_width": r_width,
                            "resize_height": r_height,
                            "classes": [],
                            "total_images": 0
                        }

                    class_projects[project_name]["classes"].append({
                        "project": project_name,
                        "label": doc.id,
                        "total_images": img_count
                    })
                    class_projects[project_name]["total_images"] += img_count

        resp = jsonify({"success": True, "data": list(class_projects.values())})
        resp.headers.add("Access-Control-Allow-Origin", "*")
        return resp, 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "data": []}), 500


# ==========================================================
# 🎯 Route สำหรับ Object Detection
# พาธบันทึก: user/{email}/detection/{project_name}
# (ตรงกับ collection "detection" ที่ /api/upload_dataset เขียนจริง)
# ==========================================================
@app.route("/get_detection_projects", methods=["POST", "OPTIONS"])
def get_detection_projects():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    try:
        body = request.get_json(silent=True) or {}
        email = body.get("email")
        if not email:
            return jsonify({"success": False, "message": "Missing email"}), 400

        projects_list = []

        # ✅ อ่านตรงจาก user/{email}/detection/{project}
        detection_docs = (
            worker_db.collection("user").document(email)
            .collection("detection").stream()
        )

        for doc in detection_docs:
            data = doc.to_dict() or {}
            projects_list.append({
                "project": doc.id,
                "project_type": "detection",
                "resize_width": data.get("resize_width", 640),
                "resize_height": data.get("resize_height", 640),
                "total_images": data.get("total_images", 0),
                "classes": []
            })

        resp = jsonify({"success": True, "data": projects_list})
        resp.headers.add("Access-Control-Allow-Origin", "*")
        return resp, 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "data": []}), 500


# ==========================================================
# ⬡ Route สำหรับ Segmentation
# พาธบันทึก: user/{email}/Segment/{project_name}
# ⚠️ ต้องใช้ collection "Segment" (S ใหญ่) ให้ตรงกับที่ /api/upload_dataset
#    และ /create_project เขียนจริง ไม่ใช่ "segmentation"
# ==========================================================
@app.route("/get_segmentation_projects", methods=["POST", "OPTIONS"])
def get_segmentation_projects():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    try:
        body = request.get_json(silent=True) or {}
        email = body.get("email")
        if not email:
            return jsonify({"success": False, "message": "Missing email"}), 400

        projects_list = []

        # ✅ อ่านตรงจาก user/{email}/Segment/{project}
        segmentation_docs = (
            worker_db.collection("user").document(email)
            .collection("Segment").stream()
        )

        for doc in segmentation_docs:
            data = doc.to_dict() or {}
            projects_list.append({
                "project": doc.id,
                "project_type": "segmentation",
                "resize_width": data.get("resize_width", 640),
                "resize_height": data.get("resize_height", 640),
                "total_images": data.get("total_images", 0),
                "classes": []
            })

        resp = jsonify({"success": True, "data": projects_list})
        resp.headers.add("Access-Control-Allow-Origin", "*")
        return resp, 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "data": []}), 500


 # =========================================================
# CREATE PROJECT
# =========================================================
 

# สมมติการตั้งค่า Firestore ตัวแปรหลักของคุณ (ปรับชื่อตามจริงของคุณ)
# db = firestore.client()

@app.route("/create_project", methods=["POST", "OPTIONS"])
def create_project():
    # จัดการ Preflight Request สำหรับ CORS
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    try:
        body = request.get_json(silent=True) or {}
        email = body.get("email")
        project_name = body.get("project")
        project_type = body.get("projectType", "classification")

        if not email or not project_name:
            resp = jsonify({"success": False, "message": "Missing email or project name"})
            resp.headers.add("Access-Control-Allow-Origin", "*")
            return resp, 400

        if project_type == "classification":
            class_name = body.get("className")
            width = body.get("resize_width", 224)
            height = body.get("resize_height", 224)

            if not class_name:
                resp = jsonify({"success": False, "message": "Missing class name for classification"})
                resp.headers.add("Access-Control-Allow-Origin", "*")
                return resp, 400

            # พาธดั้งเดิมของ Classification: /user/{email}/dataset_session/{Project}/class/{Class}
            doc_ref = worker_db.collection("user").document(email)\
                        .collection("dataset_session").document(project_name)\
                        .collection("class").document(class_name)

            doc_ref.set({
                "label": class_name,
                "total_images": 0,
                "project_type": project_type,
                "projectType": project_type,
                "resize_width": width,
                "resize_height": height
            }, merge=True)

        else:
            # 🚀 สำหรับโหมด "detection" หรือ "segmentation"
            # ✅ แก้ให้ตรงกับ path จริงที่ /api/upload_dataset เขียน:
            #    user/{email}/detection/{project}
            #    user/{email}/Segment/{project}
            # (ใช้ PROJECT_TYPE_CONFIG mapping เดียวกับ /api/upload_dataset
            #  เพื่อไม่ให้ชื่อ collection เพี้ยนกันระหว่าง 2 endpoint นี้)
            width = body.get("resize_width", 640)
            height = body.get("resize_height", 640)

            type_config = PROJECT_TYPE_CONFIG.get(project_type, PROJECT_TYPE_CONFIG["detection"])
            collection_name = type_config["collection"]  # "detection" | "Segment"

            doc_ref = worker_db.collection("user").document(email) \
                        .collection(collection_name).document(project_name)

            doc_ref.set({
                "project": project_name,
                "project_type": project_type,
                "projectType": project_type,
                "total_images": 0,
                "resize_width": width,
                "resize_height": height,
                "created_at": firestore.SERVER_TIMESTAMP
            }, merge=True)

        resp_success = jsonify({"success": True, "message": "Project created successfully"})
        resp_success.headers.add("Access-Control-Allow-Origin", "*")
        return resp_success, 200

    except Exception as e:
        traceback.print_exc()
        resp_err = jsonify({"success": False, "error": str(e)})
        resp_err.headers.add("Access-Control-Allow-Origin", "*")
        return resp_err, 500
#========================================    
# delete class
@app.route("/delete_class", methods=["POST"])
def delete_class():
    try:
        data = request.get_json(silent=True) or {}

        email = data.get("email")
        project = data.get("project")
        className = data.get("className")

        if not email or not project or not className:
            return jsonify({
                "status": "error",
                "message": "email, project, className required"
            }), 400

        doc_ref = worker_db.collection("user") \
            .document(email) \
            .collection("dataset_session") \
            .document(project) \
            .collection("class") \
            .document(className)

        doc_ref.delete()

        return jsonify({
            "status": "ok"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        })
#===============================================


@app.route("/train_dataset", methods=["POST"])
def train_dataset():

    try:

        data = request.get_json(silent=True) or {}

        device_id = data.get("deviceId")
        project = data.get("project")

        if not device_id or not project:
            return jsonify({
                "status": "error",
                "message": "deviceId and project required"
            }), 400

        # ----------------------------------
        # create training job
        # ----------------------------------
        job_ref = (
            worker_db
            .collection("user")
            .document(device_id)
            .collection("training_jobs")
            .document()
        )

        job_ref.set({
            "project": project,
            "status": "generating_csv",
            "progress": 0,
            "created_at": firestore.SERVER_TIMESTAMP  # type: ignore
        })

        job_id = job_ref.id

        # ----------------------------------
        # project doc
        # ----------------------------------
        project_ref = (
            worker_db
            .collection("user")
            .document(device_id)
            .collection("dataset_session")
            .document(project)
        )

        # ----------------------------------
        # read classes
        # ----------------------------------
        class_docs = (
            project_ref
            .collection("class")
            .stream()
        )

        csv_lines = [
            "gcs_uri,label"
        ]

        total_images = 0

        for class_doc in class_docs:

            class_name = class_doc.id

            image_docs = (
                project_ref
                .collection("class")
                .document(class_name)
                .collection("images")
                .stream()
            )

            for image_doc in image_docs:

                image_data = image_doc.to_dict() or {}

                storage_path = image_data.get("storage_path")

                if not storage_path:
                    continue

                csv_lines.append(
                    f"gs://basework-51f3b.firebasestorage.app/{storage_path},{class_name}"
                )

                total_images += 1

        # ----------------------------------
        # create csv
        # ----------------------------------
        csv_content = "\n".join(csv_lines)

        csv_path = (
            f"training_jobs/"
            f"{device_id}/"
            f"{job_id}/"
            f"dataset.csv"
        )

        blob = bucket.blob(csv_path)

        blob.upload_from_string(
            csv_content,
            content_type="text/csv"
        )

        # ----------------------------------
        # update job
        # ----------------------------------
        job_ref.update({

            "status": "csv_ready",

            "progress": 100,

            "total_images": total_images,

            "csv_path": csv_path,

            "updated_at":
                firestore.SERVER_TIMESTAMP  # type: ignore
        })

        return jsonify({

            "status": "success",

            "job_id": job_id,

            "total_images": total_images,

            "csv_path": csv_path
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

#======================================================
@app.route("/download_csv", methods=["POST"])
def download_csv():

    try:

        data = request.get_json(silent=True) or {}

        device_id = data.get("deviceId")
        job_id = data.get("jobId")

        if not device_id or not job_id:
            return jsonify({
                "status": "error",
                "message": "deviceId and jobId required"
            }), 400

        csv_path = (
            f"training_jobs/"
            f"{device_id}/"
            f"{job_id}/"
            f"dataset.csv"
        )

        blob = bucket.blob(csv_path)

        if not blob.exists():

            return jsonify({
                "status": "error",
                "message": "CSV not found"
            }), 404

        download_url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=1),
            method="GET"
        )

        return jsonify({
            "status": "success",
            "download_url": download_url
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
        #===============================
def save_image_document(

    email,

    project,

    class_name,

    upload_result,

    width,

    height,

    file_size,

    camera_source,

    capture_mode,

    augmentation="original"

):

    doc_ref = (

        worker_db
        .collection("user")
        .document(email)
        .collection("dataset_session")
        .document(project)
        .collection("class")
        .document(class_name)
        .collection("images")
        .document()

    )

    doc_ref.set({

        "filename":
            upload_result["filename"],

        "storagePath":
            upload_result["storagePath"],

        "imageUrl":
            upload_result["imageUrl"],

        "width":
            width,

        "height":
            height,

        "file_size":
            file_size,

        "camera_source":
            camera_source,

        "capture_mode":
            capture_mode,

        "augmentation":
            augmentation,

        "created_at":
            firestore.SERVER_TIMESTAMP  # type: ignore

    })
         #===================================================
def update_firestore(

    email,

    project,

    class_name,

    increase=1,

    total_size=0

):

    # ==========================
    # Firestore Document
    # ==========================

    doc_ref = (

        worker_db
        .collection("user")
        .document(email)
        .collection("dataset_session")
        .document(project)
        .collection("class")
        .document(class_name)

    )

    # ==========================
    # Read Current Data
    # ==========================

    doc = doc_ref.get()

    if doc.exists:

        current = doc.to_dict() or {}

        total_images = current.get(

            "total_images",

            0

        )

        class_size = current.get(

            "total_size",

            0

        )

    else:

        total_images = 0

        class_size = 0

    # ==========================
    # Update Values
    # ==========================

    total_images += increase

    class_size += total_size

    # ==========================
    # Save Firestore
    # ==========================

    doc_ref.set(

        {

            "project": project,

            "label": class_name,

            "total_images": total_images,

            "total_size": class_size,

            "updated_at":
                firestore.SERVER_TIMESTAMP  # type: ignore

        },

        merge=True

    )

    # ==========================
    # Return Summary
    # ==========================

    return {

        "totalImages":
            total_images,

        "classSize":
            class_size,

        "classSizeKB":
            round(class_size / 1024, 1),

        "classSizeMB":
            round(class_size / 1024 / 1024, 2)

    }

          #===================================================
def upload_image(

    image,

    email,

    project,

    class_name,

    camera_source,

    image_type="original"

):

    # ==========================
    # Generate Filename
    # ==========================

    filename = f"{uuid.uuid4().hex}.jpg"

    # ==========================
    # Storage Path
    # ==========================
    storage_path = (

        f"{email}/"

        f"{project}/"

        f"class/"

        f"{class_name}/"

        f"{filename}"

    )

    # ==========================
    # Convert PIL -> JPEG Bytes
    # ==========================

    buffer = io.BytesIO()

    image.save(

        buffer,

        format="JPEG",

        quality=95

    )

    buffer.seek(0)

    # ==========================
    # Upload Firebase Storage
    # ==========================

    blob = bucket.blob(

        storage_path

    )

    blob.upload_from_file(

        buffer,

        content_type="image/jpeg"

    )

    # ==========================
    # Make Public (Optional)
    # ==========================

    blob.make_public()

    image_url = blob.public_url

    # ==========================
    # Return
    # ==========================

    return {

        "filename": filename,

        "storagePath": storage_path,

        "imageUrl": image_url,

        "cameraSource": camera_source,

        "imageType": image_type

    }
           #===================================================

def resize_image(image, width, height):

    return image.resize(

        (
            int(width),

            int(height)

        ),

        Image.Resampling.LANCZOS

    )
#=================================================
def generate_images(image):

    images = []

    width, height = image.size

    # =====================================
    # Original
    # =====================================

    images.append(

        ("original", image.copy())

    )

    # =====================================
    # Rotation
    # =====================================

    images.append(

        (
            "rotation_-10",

            image.rotate(
                -10,
                expand=False,
                fillcolor=(0, 0, 0)
            )

        )

    )

    images.append(

        (
            "rotation_10",

            image.rotate(
                10,
                expand=False,
                fillcolor=(0, 0, 0)
            )

        )

    )

    # =====================================
    # Zoom In
    # =====================================

    crop = image.crop(

        (

            width * 0.10,

            height * 0.10,

            width * 0.90,

            height * 0.90

        )

    )

    crop = crop.resize(

        (width, height),

        Image.Resampling.LANCZOS

    )

    images.append(

        ("zoom_in", crop)

    )

    # =====================================
    # Zoom Out
    # =====================================

    zoom = image.resize(

        (

            int(width * 0.80),

            int(height * 0.80)

        ),

        Image.Resampling.LANCZOS

    )

    canvas = Image.new(

        "RGB",

        (width, height),

        (0, 0, 0)

    )

    x = (width - zoom.width) // 2
    y = (height - zoom.height) // 2

    canvas.paste(

        zoom,

        (x, y)

    )

    images.append(

        ("zoom_out", canvas)

    )

    # =====================================
    # Brightness Dark
    # =====================================

    dark = ImageEnhance.Brightness(

        image

    ).enhance(0.7)

    images.append(

        ("brightness_dark", dark)

    )

    # =====================================
    # Brightness Bright
    # =====================================

    bright = ImageEnhance.Brightness(

        image

    ).enhance(1.3)

    images.append(

        ("brightness_bright", bright)

    )

    # =====================================
    # Translation Left
    # =====================================

    images.append(

        (

            "translate_left",

            ImageChops.offset(

                image,

                -20,

                0

            )

        )

    )

    # =====================================
    # Translation Right
    # =====================================

    images.append(

        (

            "translate_right",

            ImageChops.offset(

                image,

                20,

                0

            )

        )

    )

    # =====================================
    # Translation Up
    # =====================================

    images.append(

        (

            "translate_up",

            ImageChops.offset(

                image,

                0,

                -20

            )

        )

    )

    # =====================================
    # Translation Down
    # =====================================

    images.append(

        (

            "translate_down",

            ImageChops.offset(

                image,

                0,

                20

            )

        )

    )

    return images
 #===================================================

def decode_base64(image_base64):

    # ==========================
    # Remove Header
    # ==========================

    if "," in image_base64:

        image_base64 = image_base64.split(",")[1]

    # ==========================
    # Decode Base64
    # ==========================

    image_bytes = base64.b64decode(
        image_base64
    )

    # ==========================
    # Convert to PIL
    # ==========================

    image = Image.open(

        io.BytesIO(
            image_bytes
        )

    ).convert("RGB")

    return image
 #===================================================
# Quota Exception
# ============================================================
class QuotaExceededError(Exception):
    """โยน error นี้เมื่อ user เกินโควต้าของแผนตัวเอง"""
    pass


# ============================================================
# ตรวจ + จอง (reserve) โควต้าแบบ atomic ด้วย Firestore Transaction
# ป้องกันกรณีอัปโหลดพร้อมกันหลาย request จนนับ usage ผิด
# ============================================================
@firestore.transactional
def _reserve_quota_txn(transaction, plan_ref, plans_col_ref, add_images, add_bytes):

    plan_snap = plan_ref.get(transaction=transaction)

    if not plan_snap.exists:
        raise QuotaExceededError("ไม่พบข้อมูลแผนของผู้ใช้ (plan/select)")

    plan_data = plan_snap.to_dict()
    plan_name = plan_data.get("plan", "free")
    usage = plan_data.get("usage", {})

    current_images = usage.get("totalImages", 0)
    current_bytes = usage.get("totalStorageBytes", 0)

    limit_snap = plans_col_ref.document(plan_name).get(transaction=transaction)

    if not limit_snap.exists:
        raise QuotaExceededError(f"ไม่พบ config ของแผน '{plan_name}'")

    limits = limit_snap.to_dict()
    max_images = limits.get("maxImages")          # None = unlimited
    max_storage = limits.get("maxStorageBytes")   # None = unlimited

    if max_images is not None and (current_images + add_images) > max_images:
        raise QuotaExceededError(
            f"เกินโควต้าจำนวนรูปของแผน {plan_name} "
            f"({current_images}/{max_images} รูป)"
        )

    if max_storage is not None and (current_bytes + add_bytes) > max_storage:
        raise QuotaExceededError(
            f"พื้นที่จัดเก็บไม่พอสำหรับแผน {plan_name} "
            f"({current_bytes/1024/1024:.1f}MB / {max_storage/1024/1024:.1f}MB)"
        )

    # ผ่าน -> จองโควต้าไว้เลยในทรานแซคชันเดียวกัน
    transaction.update(plan_ref, {
        "usage.totalImages": firestore.Increment(add_images),
        "usage.totalStorageBytes": firestore.Increment(add_bytes),
        "usage.lastUpdated": firestore.SERVER_TIMESTAMP
    })


def reserve_quota(email, add_images, add_bytes):
    """
    เรียกก่อนอัปโหลดรูปทุกครั้ง (ทีละรูป)
    ถ้าเกินโควต้า -> raise QuotaExceededError (ให้ route จับแล้วตอบ 403)
    ถ้าผ่าน -> usage ของ user/{email}/plan/select ถูก +1 รูป และ +ขนาดไฟล์ ให้ทันที
    """
    plan_ref = (
        worker_db.collection("user")
        .document(email)
        .collection("plan")
        .document("select")
    )

    plans_col_ref = worker_db.collection("plans")
    transaction = worker_db.transaction()

    _reserve_quota_txn(transaction, plan_ref, plans_col_ref, add_images, add_bytes)


# ============================================================
# Single Capture
# ============================================================
def upload_single(data):

    email = data["email"]
    project = data["project"]
    class_name = data["className"]
    resize_width = int(data["resizeWidth"])
    resize_height = int(data["resizeHeight"])
    camera_source = data.get("cameraSource", "browser")
    image_base64 = data["image"]

    image = decode_base64(image_base64)
    image = resize_image(image, resize_width, resize_height)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    file_size = buffer.tell()

    # ------------------------------------------------
    # ✅ เช็คโควต้าแผน "ก่อน" อัปโหลดขึ้น Storage จริง
    # ------------------------------------------------
    reserve_quota(email=email, add_images=1, add_bytes=file_size)

    upload_result = upload_image(
        image=image,
        email=email,
        project=project,
        class_name=class_name,
        camera_source=camera_source,
        image_type="original"
    )

    save_image_document(
        email=email,
        project=project,
        class_name=class_name,
        upload_result=upload_result,
        width=resize_width,
        height=resize_height,
        file_size=file_size,
        camera_source=camera_source,
        capture_mode="single",
        augmentation="original"
    )

    summary = update_firestore(
        email=email,
        project=project,
        class_name=class_name,
        increase=1,
        total_size=file_size
    )

    return {
        "success": True,
        "captureMode": "single",
        "filename": upload_result["filename"],
        "storagePath": upload_result["storagePath"],
        "imageUrl": upload_result["imageUrl"],
        "cameraSource": camera_source,
        "totalImages": summary["totalImages"],
        "classSize": summary["classSize"],
        "classSizeKB": summary["classSizeKB"],
        "classSizeMB": summary["classSizeMB"],
        "width": resize_width,
        "height": resize_height,
        "fileSize": file_size,
        "fileSizeKB": round(file_size / 1024, 1)
    }


# ============================================================
# Burst Capture
# ============================================================
def upload_burst(data):

    email = data["email"]
    project = data["project"]
    class_name = data["className"]
    resize_width = int(data["resizeWidth"])
    resize_height = int(data["resizeHeight"])
    camera_source = data.get("cameraSource", "browser")
    images = data["images"]

    uploaded_files = []
    total_size = 0
    quota_message = None   # ถ้าเกินโควต้ากลางทาง จะหยุดแล้วเก็บข้อความไว้ตรงนี้

    for index, image_base64 in enumerate(images):

        image = decode_base64(image_base64)
        image = resize_image(image, resize_width, resize_height)

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        file_size = buffer.tell()

        # ------------------------------------------------
        # ✅ เช็คโควต้าทีละรูป ก่อนอัปโหลดรูปนั้นขึ้น Storage
        #    ถ้ารูปที่ N เกินโควต้า -> หยุด burst ตรงนี้เลย
        #    (รูปที่ 1..N-1 ที่อัปโหลดไปแล้วยังเก็บไว้ ไม่ rollback)
        # ------------------------------------------------
        try:
            reserve_quota(email=email, add_images=1, add_bytes=file_size)
        except QuotaExceededError as e:
            quota_message = str(e)
            break

        total_size += file_size

        upload_result = upload_image(
            image=image,
            email=email,
            project=project,
            class_name=class_name,
            camera_source=camera_source,
            image_type=f"burst_{index+1}"
        )

        save_image_document(
            email=email,
            project=project,
            class_name=class_name,
            upload_result=upload_result,
            width=resize_width,
            height=resize_height,
            file_size=file_size,
            camera_source=camera_source,
            capture_mode="burst",
            augmentation=f"burst_{index+1}"
        )

        uploaded_files.append({
            "type": f"burst_{index+1}",
            "filename": upload_result["filename"],
            "storagePath": upload_result["storagePath"],
            "imageUrl": upload_result["imageUrl"],
            "fileSize": file_size,
            "fileSizeKB": round(file_size / 1024, 1)
        })

    # ถ้าไม่มีรูปไหนอัปโหลดผ่านเลยสักรูป (โดนบล็อกตั้งแต่รูปแรก)
    if not uploaded_files:
        raise QuotaExceededError(quota_message or "ไม่สามารถอัปโหลดได้")

    summary = update_firestore(
        email=email,
        project=project,
        class_name=class_name,
        increase=len(uploaded_files),
        total_size=total_size
    )

    average_size = round(total_size / len(uploaded_files)) if uploaded_files else 0

    result = {
        "success": True,
        "captureMode": "burst",
        "uploaded": len(uploaded_files),
        "cameraSource": camera_source,
        "width": resize_width,
        "height": resize_height,
        "totalImages": summary["totalImages"],
        "classSize": summary["classSize"],
        "classSizeKB": summary["classSizeKB"],
        "classSizeMB": summary["classSizeMB"],
        "totalSize": total_size,
        "totalSizeKB": round(total_size / 1024, 1),
        "averageSize": average_size,
        "averageSizeKB": round(average_size / 1024, 1),
        "files": uploaded_files
    }

    # แจ้งฝั่ง frontend ว่า burst ถูกตัดตอนก่อนครบ เพราะเกินโควต้า
    if quota_message:
        result["quotaExceeded"] = True
        result["quotaMessage"] = quota_message

    return result


# ============================================================
# AI Dataset Generator
# ============================================================
def upload_generator(data):

    email = data["email"]
    project = data["project"]
    class_name = data["className"]
    resize_width = int(data["resizeWidth"])
    resize_height = int(data["resizeHeight"])
    camera_source = data.get("cameraSource", "browser")
    image_base64 = data["image"]

    image = decode_base64(image_base64)
    image = resize_image(image, resize_width, resize_height)

    generated_images = generate_images(image)

    uploaded_files = []
    total_size = 0
    quota_message = None

    for image_type, img in generated_images:

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=95)
        file_size = buffer.tell()

        # ------------------------------------------------
        # ✅ เช็คโควต้าทีละรูปที่ generate ออกมา
        # ------------------------------------------------
        try:
            reserve_quota(email=email, add_images=1, add_bytes=file_size)
        except QuotaExceededError as e:
            quota_message = str(e)
            break

        total_size += file_size

        upload_result = upload_image(
            image=img,
            email=email,
            project=project,
            class_name=class_name,
            camera_source=camera_source,
            image_type=image_type
        )

        save_image_document(
            email=email,
            project=project,
            class_name=class_name,
            upload_result=upload_result,
            width=resize_width,
            height=resize_height,
            file_size=file_size,
            camera_source=camera_source,
            capture_mode="generator",
            augmentation=image_type
        )

        uploaded_files.append({
            "type": image_type,
            "filename": upload_result["filename"],
            "storagePath": upload_result["storagePath"],
            "imageUrl": upload_result["imageUrl"],
            "fileSize": file_size,
            "fileSizeKB": round(file_size / 1024, 1)
        })

    if not uploaded_files:
        raise QuotaExceededError(quota_message or "ไม่สามารถอัปโหลดได้")

    summary = update_firestore(
        email=email,
        project=project,
        class_name=class_name,
        increase=len(uploaded_files),
        total_size=total_size
    )

    average_size = round(total_size / len(uploaded_files)) if uploaded_files else 0

    result = {
        "success": True,
        "captureMode": "generator",
        "generated": len(uploaded_files),
        "cameraSource": camera_source,
        "width": resize_width,
        "height": resize_height,
        "totalImages": summary["totalImages"],
        "classSize": summary["classSize"],
        "classSizeKB": summary["classSizeKB"],
        "classSizeMB": summary["classSizeMB"],
        "totalSize": total_size,
        "totalSizeKB": round(total_size / 1024, 1),
        "averageSize": average_size,
        "averageSizeKB": round(average_size / 1024, 1),
        "files": uploaded_files
    }

    if quota_message:
        result["quotaExceeded"] = True
        result["quotaMessage"] = quota_message

    return result


# ============================================================
# Route
# ============================================================
@app.route("/upload_dataset_image", methods=["POST"])
def upload_dataset_image():

    try:
        data = request.get_json(silent=True) or {}

        if not data:
            return jsonify({"success": False, "message": "No JSON data"}), 400

        capture_mode = data.get("captureMode", "single").lower()

        if capture_mode == "single":
            result = upload_single(data)
        elif capture_mode == "burst":
            result = upload_burst(data)
        elif capture_mode == "generator":
            result = upload_generator(data)
        else:
            return jsonify({
                "success": False,
                "message": f"Unknown captureMode : {capture_mode}"
            }), 400

        return jsonify(result)

    # ------------------------------------------------
    # ✅ เกินโควต้า -> ตอบ 403 แยกจาก error ทั่วไป (500)
    #    ฝั่ง frontend เช็ค response.ok == False + message นี้
    #    เพื่อแจ้งเตือนให้ user ไปหน้า /pricing
    # ------------------------------------------------
    except QuotaExceededError as qe:
        return jsonify({
            "success": False,
            "message": str(qe),
            "quotaExceeded": True
        }), 403

    except Exception as ex:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(ex)}), 500


# ============================================================
# PLAN LIMITS CONFIG (ใช้ตอน seed collection "plans")
# field ต้องตรงกับที่ _reserve_quota_txn อ่าน:
#   limits.get("maxImages")
#   limits.get("maxStorageBytes")
# None = unlimited
#
# ⚠️ ชื่อ key ต้องตรงกับค่า field "plan" ที่เก็บจริงใน
#    user/{email}/plan/select แบบตัวพิมพ์ใหญ่-เล็กเป๊ะๆ
#    (จาก Firestore จริงตอนนี้คือ "Free")
# ============================================================
PLANS_SEED = {

    "Free": {
        "maxImages": 500,
        "maxStorageBytes": 500 * 1024 * 1024,            # 500 MB
    },

    "Starter": {
        "maxImages": 30000,
        "maxStorageBytes": 30 * 1024 * 1024 * 1024,      # 30 GB
    },

    "Pro": {
        "maxImages": 300000,
        "maxStorageBytes": 150 * 1024 * 1024 * 1024,     # 150 GB
    },

    "Business": {
        "maxImages": 1000000,
        "maxStorageBytes": 500 * 1024 * 1024 * 1024,     # 500 GB
    },

}


# ==========================================================
# PLAN CONFIG สำหรับเขียนลง user/{email}/plan/select.limits
# (ให้ตรงกับหน้า Pricing.jsx: storage / projects / images)
# ==========================================================
PLAN_CONFIG = {

    "Free": {
        "maxProjects": 1,
        "maxImages": 500,
        "storageBytes": 500 * 1024 * 1024,          # 500 MB
    },

    "Starter": {
        "maxProjects": None,                         # Unlimited
        "maxImages": 30000,                           # 30,000 Images / Month
        "storageBytes": 30 * 1024 * 1024 * 1024,      # 30 GB
    },

    "Pro": {
        "maxProjects": None,                          # Unlimited
        "maxImages": 300000,                          # 300,000 Images / Month
        "storageBytes": 150 * 1024 * 1024 * 1024,     # 150 GB
    },

    "Business": {
        "maxProjects": None,                          # Unlimited
        "maxImages": 1000000,                         # 1,000,000 Images / Month
        "storageBytes": 500 * 1024 * 1024 * 1024,     # 500 GB
    },

}


# ==========================================================
# SEED / INIT PLANS COLLECTION
# เรียกครั้งเดียว (หรือเรียกซ้ำได้ เพราะใช้ merge=True) เพื่อสร้าง
# worker_db/plans/{planName}  ให้ reserve_quota() อ่านค่าถูกต้อง
# 🔒 admin only -> ต้องแนบ header X-Admin-Key
# ==========================================================
@app.route("/init_plans", methods=["POST"])
@require_admin_key
def init_plans():

    try:

        plans_col_ref = worker_db.collection("plans")

        created = []

        for plan_name, limits in PLANS_SEED.items():

            plan_doc_ref = plans_col_ref.document(plan_name)

            plan_doc_ref.set({

                "maxImages":
                    limits["maxImages"],

                "maxStorageBytes":
                    limits["maxStorageBytes"],

                "updated_at":
                    firestore.SERVER_TIMESTAMP  # type: ignore

            }, merge=True)

            created.append(plan_name)

        return jsonify({

            "status": "success",

            "message": "plans collection updated",

            "plans": created

        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ==========================================================
# (Optional) ดูค่า plans ปัจจุบันทั้งหมด เพื่อเช็คว่าตรงกับที่ตั้งใจไหม
# 🔒 admin only -> ต้องแนบ header X-Admin-Key
# ==========================================================
@app.route("/get_plans", methods=["GET"])
@require_admin_key
def get_plans():

    try:

        result = {}

        docs = worker_db.collection("plans").stream()

        for doc in docs:

            result[doc.id] = doc.to_dict()

        return jsonify({

            "status": "success",

            "plans": result

        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ==========================================================
# UPDATE PLAN (เรียกหลังจ่ายเงินสำเร็จที่หน้า /checkout)
# แนะนำให้เรียกจาก backend/webhook หลัง confirm การจ่ายเงินจริง
# ไม่ควรเรียกตรงจาก browser เพราะ ADMIN_SECRET_KEY จะหลุดจาก bundle
# 🔒 admin only -> ต้องแนบ header X-Admin-Key
# ==========================================================
@app.route("/update_plan", methods=["POST"])
@require_admin_key
def update_plan():

    try:

        data = request.get_json(silent=True) or {}

        email = (
            data.get("email", "")
            .lower()
            .strip()
        )

        # เผื่อ frontend ส่งมาเป็น "Pro ⭐" ตัด emoji/space ออก
        plan_name = (
            data.get("plan", "")
            .replace("⭐", "")
            .strip()
        )

        if not email:
            return jsonify({
                "status": "error",
                "message": "no email"
            }), 400

        if plan_name not in PLAN_CONFIG:
            return jsonify({
                "status": "error",
                "message": f"invalid plan: {plan_name}"
            }), 400

        limits = PLAN_CONFIG[plan_name]

        user_ref = (
            worker_db
            .collection("user")
            .document(email)
        )

        if not user_ref.get().exists:
            return jsonify({
                "status": "error",
                "message": "user not found"
            }), 404

        plan_ref = (
            user_ref
            .collection("plan")
            .document("select")
        )

        new_expire = (
            datetime.utcnow()
            + timedelta(days=30)
        )

        # merge=True -> ไม่แตะ usage.totalImages / totalStorageBytes เดิม
        plan_ref.set({

            "email": email,

            "plan": plan_name,

            "status": "active",

            "paymentStatus": "paid",

            "paidAt":
                firestore.SERVER_TIMESTAMP,

            "expireAt": new_expire,

            "limits": {

                "maxProjects":
                    limits["maxProjects"],

                "maxImages":
                    limits["maxImages"],

                "storageBytes":
                    limits["storageBytes"],

            }

        }, merge=True)

        return jsonify({

            "status": "success",

            "message":
                f"อัปเกรดเป็นแผน {plan_name} สำเร็จ",

            "plan": plan_name,

            "limits": limits

        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

#===========================================

#================= training job status ==========================
# ==========================================================
# ไฟล์นี้คือ "ส่วนที่ต้องแก้ไข/เพิ่ม" ใน backend (Flask) เดิมของคุณ
# ไม่ใช่ไฟล์สมบูรณ์ที่รันได้เดี่ยวๆ ให้ copy ส่วนที่เกี่ยวข้อง
# ไปแทนที่/เพิ่มใน app.py เดิม
#
# ต้องติดตั้ง library เพิ่ม:
#   pip install tensorflowjs tf2onnx onnx
# ==========================================================
from tensorflow.keras.applications import MobileNetV2  # type: ignore
from tensorflow.keras import layers, models  # type: ignore
from tensorflow.keras.optimizers import Adam  # type: ignore

import tf2onnx
# หมายเหตุ: ยังไม่รองรับ tfjs ใน service นี้ เพราะ tensorflowjs (python)
# ชน dependency กับ tensorflow==2.12.0 (protobuf conflict)
# วางแผนแยกเป็น service ต่างหากในอนาคต

training_status = {}   # key = f"{email}/{project}"

VALID_FORMATS = ("tfjs", "tflite", "onnx")
# 'tfjs' ไม่ convert ในนี้โดยตรงแล้ว (เพราะ tensorflowjs ชน dependency
# กับ tensorflow==2.12.0) แต่จะ save เป็น SavedModel แล้วเรียกไปที่
# TFJS_SERVICE_URL (service แยกที่ pin เวอร์ชันของตัวเอง) ให้ convert ให้แทน
TFJS_SERVICE_URL = os.environ.get("TFJS_SERVICE_URL")

if not TFJS_SERVICE_URL:
    raise RuntimeError("Missing TFJS_SERVICE_URL")


# ==========================================================
# 1) /train_project : เพิ่มรับ field "format"
# ==========================================================
@app.route("/train_project", methods=["POST"])
def train_project():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    export_format = data.get("format", "tflite")

    if export_format not in VALID_FORMATS:
        return jsonify({
            "success": False,
            "message": f"Invalid format, must be one of {VALID_FORMATS}"
        }), 400

    if not email or not project:
        return jsonify({
            "success": False,
            "message": "Missing email or project"
        }), 400

    prefix = f"{email}/{project}/class/"
    blobs = list(bucket.list_blobs(prefix=prefix))

    if not blobs:
        return jsonify({
            "success": False,
            "message": "No training data found"
        }), 404

    key = f"{email}/{project}"
    training_status[key] = {
        "status": "running",
        "progress": 0,
        "format": export_format
    }

    thread = threading.Thread(
        target=run_training,
        args=(email, project, blobs, export_format)
    )
    thread.start()

    return jsonify({
        "success": True,
        "message": "Training started"
    })


@app.route("/train_status", methods=["POST"])
def train_status():
    data = request.get_json(silent=True) or {}
    key = f"{data.get('email')}/{data.get('project')}"
    return jsonify(training_status.get(
        key, {"status": "idle"}
    ))


# ==========================================================
# 2) run_training : เพิ่ม export_format และแตกกิ่ง convert ตอนจบ
# ==========================================================
def run_training(email, project, blobs, export_format):
    key = f"{email}/{project}"
    prefix = f"{email}/{project}/class/"

    try:
        # ---------- 1. โหลดรูปตาม class (เหมือนเดิมทุกอย่าง) ----------
        images = []
        labels = []
        class_names = []

        for blob in blobs:
            relative = blob.name[len(prefix):]
            parts = relative.split("/")

            if len(parts) < 2:
                continue

            label = parts[0]
            filename = parts[1]

            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                continue

            if label not in class_names:
                class_names.append(label)

            img_bytes = blob.download_as_bytes()
            img = Image.open(BytesIO(img_bytes)).convert("RGB")
            img = img.resize((224, 224))

            images.append(np.array(img) / 255.0)
            labels.append(class_names.index(label))

        if len(images) == 0:
            training_status[key] = {
                "status": "error",
                "message": "No valid images found"
            }
            return

        X = np.array(images, dtype=np.float32)
        y = tf.keras.utils.to_categorical(
            labels, num_classes=len(class_names)
        )

        training_status[key]["progress"] = 20

        # ---------- 2. Build Model (เหมือนเดิม) ----------
        base_model = MobileNetV2(
            input_shape=(224, 224, 3),
            include_top=False,
            weights="imagenet"
        )
        base_model.trainable = False

        model = models.Sequential([
            base_model,
            layers.GlobalAveragePooling2D(),
            layers.Dense(128, activation="relu"),
            layers.Dropout(0.3),
            layers.Dense(len(class_names), activation="softmax")
        ])

        model.compile(
            optimizer=Adam(learning_rate=1e-4),
            loss="categorical_crossentropy",
            metrics=["accuracy"]
        )

        # ---------- 3. Train (เหมือนเดิม) ----------
        class ProgressCallback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                logs = logs or {}
                pct = 20 + int(((epoch + 1) / EPOCHS) * 60)
                training_status[key]["progress"] = pct
                training_status[key]["accuracy"] = float(
                    logs.get("accuracy", 0)
                )

        EPOCHS = 10
        model.fit(
            X, y,
            epochs=EPOCHS,
            batch_size=16,
            validation_split=0.2,
            callbacks=[ProgressCallback()],
            verbose=1
        )

        training_status[key]["progress"] = 85

        # ---------- 4. Convert -> ตาม format ที่เลือก ----------
        model_dir = f"{email}/{project}/model/{export_format}"

        if export_format == "tflite":
            converter = tf.lite.TFLiteConverter.from_keras_model(model)
            tflite_model = converter.convert()

            model_blob = bucket.blob(f"{model_dir}/model.tflite")
            model_blob.upload_from_string(
                tflite_model,
                content_type="application/octet-stream"
            )

        elif export_format == "onnx":
            spec = (tf.TensorSpec(
                (None, 224, 224, 3), tf.float32, name="input"
            ),)
            output_path = "/tmp/model.onnx"

            tf2onnx.convert.from_keras(
                model,
                input_signature=spec,
                output_path=output_path
            )

            model_blob = bucket.blob(f"{model_dir}/model.onnx")
            model_blob.upload_from_filename(output_path)

        elif export_format == "tfjs":
            # ไม่ convert ที่นี่ เพราะ tensorflowjs ชน dependency กับ
            # tensorflow==2.12.0 (ดู requirements.txt ของ service นี้)
            # แทนที่ด้วยการ save เป็น SavedModel แล้วส่งไปให้ service แยกแปลงให้

            saved_model_dir = "/tmp/saved_model"
            model.save(
                saved_model_dir,
                save_format="tf",
                include_optimizer=False,
                options=tf.saved_model.SaveOptions(
                    experimental_custom_gradients=False
                )
            )

            saved_model_prefix = f"{email}/{project}/model/saved_model/"
            for root, _, files in os.walk(saved_model_dir):
                for fname in files:
                    local_path = os.path.join(root, fname)
                    relative = os.path.relpath(local_path, saved_model_dir)
                    blob = bucket.blob(
                        f"{saved_model_prefix}{relative.replace(os.sep, '/')}"
                    )
                    blob.upload_from_filename(local_path)

            training_status[key]["progress"] = 92

            # เรียก service แยกให้ convert SavedModel -> tfjs ให้
            resp = requests.post(
                f"{TFJS_SERVICE_URL}/convert",
                json={"email": email, "project": project},
                timeout=300
            )

            try:
                result = resp.json()
            except ValueError:
                raise RuntimeError(
                    f"tfjs-converter service ตอบกลับผิดปกติ "
                    f"(status {resp.status_code}): {resp.text[:300]}"
                )

            if not result.get("success"):
                raise RuntimeError(
                    f"tfjs conversion failed: {result.get('message')}"
                )



        # labels.json ใช้ร่วมกันทุก format
        labels_path = f"{email}/{project}/model/labels.json"
        labels_blob = bucket.blob(labels_path)
        labels_blob.upload_from_string(
            json.dumps(class_names),
            content_type="application/json"
        )

        training_status[key] = {
            "status": "done",
            "progress": 100,
            "classes": class_names,
            "num_images": len(images),
            "format": export_format
        }

    except Exception as e:
        traceback.print_exc()
        training_status[key] = {
            "status": "error",
            "message": str(e)
        }


# ==========================================================
# 3) /download_model : endpoint ใหม่ - zip ไฟล์โมเดล + labels ส่งกลับ
# ==========================================================
@app.route("/download_model", methods=["POST"])
def download_model():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    export_format = data.get("format")

    if not email or not project or export_format not in VALID_FORMATS:
        return jsonify({
            "success": False,
            "message": "Invalid request"
        }), 400

    model_prefix = f"{email}/{project}/model/{export_format}/"
    labels_path = f"{email}/{project}/model/labels.json"

    blobs = list(bucket.list_blobs(prefix=model_prefix))
    if not blobs:
        return jsonify({
            "success": False,
            "message": "Model not found, กรุณา train ก่อน"
        }), 404

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for blob in blobs:
            arcname = blob.name[len(model_prefix):]
            zf.writestr(arcname, blob.download_as_bytes())

        labels_blob = bucket.blob(labels_path)
        if labels_blob.exists():
            zf.writestr("labels.json", labels_blob.download_as_bytes())

    zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{project}_{export_format}.zip"
    )

# =========================================================
# EXPORT DATASET (Object Detection / Segmentation)
# ⚠️ นี่ไม่ใช่ "เทรนโมเดล" จริง — ระบบยังไม่มี training pipeline สำหรับ
#    Detection/Segmentation (มีแค่ Classification ที่ /train_project ด้านบน)
#    ปุ่มนี้แค่แปลง annotation (bounding_boxes / polygons) ที่เก็บใน Firestore
#    ให้เป็นฟอร์แมตมาตรฐานอุตสาหกรรม (YOLO หรือ COCO) พร้อมรูปภาพ แล้ว zip ให้โหลด
#    ไปเทรนต่อเองด้วยเครื่องมืออื่น (เช่น Ultralytics YOLOv8, Detectron2, Colab)
#
# path อ่านข้อมูล (ตรงกับที่ /api/upload_dataset เขียน):
#   detection    -> user/{email}/detection/{project}/images/{doc_id}
#   segmentation -> user/{email}/Segment/{project}/images/{doc_id}
# =========================================================
def _yolo_normalize_bbox(box, img_w, img_h):
    x_center = (box["x"] + box["w"] / 2) / img_w
    y_center = (box["y"] + box["h"] / 2) / img_h
    return x_center, y_center, box["w"] / img_w, box["h"] / img_h


def _yolo_normalize_polygon(points, img_w, img_h):
    return [(p["x"] / img_w, p["y"] / img_h) for p in points]


@app.route("/export_dataset", methods=["POST", "OPTIONS"])
def export_dataset():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    try:
        data = request.get_json(silent=True) or {}
        email = data.get("email")
        project = data.get("project")
        task_type = (data.get("taskType") or "detection").strip().lower()
        export_format = (data.get("format") or "yolo").strip().lower()

        if not email or not project:
            return jsonify({"success": False, "message": "Missing email or project"}), 400

        if export_format not in ("yolo", "coco"):
            return jsonify({"success": False, "message": "format must be 'yolo' or 'coco'"}), 400

        is_segmentation = task_type == "segmentation"
        collection_name = "Segment" if is_segmentation else "detection"

        image_docs = list(
            worker_db.collection("user").document(email)
            .collection(collection_name).document(project)
            .collection("images").stream()
        )

        if not image_docs:
            return jsonify({
                "success": False,
                "message": "ไม่พบรูปภาพในโปรเจกต์นี้ กรุณาบันทึกรูปภาพอย่างน้อย 1 รูปก่อน Export"
            }), 404

        # เก็บรายชื่อ class ทั้งหมด (อิง label ของแต่ละกล่อง/polygon จริง ไม่ใช่แค่ class_name ของภาพ)
        class_names = []

        def get_class_id(name):
            if name not in class_names:
                class_names.append(name)
            return class_names.index(name)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

            coco_images = []
            coco_annotations = []
            ann_id = 1

            for idx, doc in enumerate(image_docs):
                d = doc.to_dict() or {}
                storage_path = d.get("storage_path")
                img_w = d.get("image_width")
                img_h = d.get("image_height")
                filename = d.get("image_filename") or f"{doc.id}.jpg"

                if not storage_path or not img_w or not img_h:
                    continue  # ข้ามเอกสารที่ข้อมูลไม่ครบ (กันพัง ไม่ให้ export ทั้งชุดล้มเพราะรูปเดียว)

                try:
                    img_bytes = bucket.blob(storage_path).download_as_bytes()
                except Exception:
                    continue  # ไฟล์รูปหายจาก Storage ก็ข้ามไป ไม่ให้ทั้ง export ล้ม

                zf.writestr(f"images/{filename}", img_bytes)

                if export_format == "yolo":
                    lines = []
                    if is_segmentation:
                        for poly in d.get("polygons", []):
                            cid = get_class_id(poly.get("label", "object"))
                            norm_pts = _yolo_normalize_polygon(poly.get("points", []), img_w, img_h)
                            coords = " ".join(f"{x:.6f} {y:.6f}" for x, y in norm_pts)
                            lines.append(f"{cid} {coords}")
                    else:
                        for box in d.get("bounding_boxes", []):
                            cid = get_class_id(box.get("label", "object"))
                            xc, yc, wn, hn = _yolo_normalize_bbox(box, img_w, img_h)
                            lines.append(f"{cid} {xc:.6f} {yc:.6f} {wn:.6f} {hn:.6f}")

                    label_filename = os.path.splitext(filename)[0] + ".txt"
                    zf.writestr(f"labels/{label_filename}", "\n".join(lines))

                else:  # coco
                    image_id = idx + 1
                    coco_images.append({
                        "id": image_id,
                        "file_name": f"images/{filename}",
                        "width": img_w,
                        "height": img_h
                    })

                    if is_segmentation:
                        for poly in d.get("polygons", []):
                            cid = get_class_id(poly.get("label", "object"))
                            pts = poly.get("points", [])
                            if not pts:
                                continue
                            xs = [p["x"] for p in pts]
                            ys = [p["y"] for p in pts]
                            seg_flat = []
                            for p in pts:
                                seg_flat.extend([p["x"], p["y"]])
                            bbox = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
                            coco_annotations.append({
                                "id": ann_id,
                                "image_id": image_id,
                                "category_id": cid + 1,
                                "segmentation": [seg_flat],
                                "bbox": bbox,
                                "area": bbox[2] * bbox[3],
                                "iscrowd": 0
                            })
                            ann_id += 1
                    else:
                        for box in d.get("bounding_boxes", []):
                            cid = get_class_id(box.get("label", "object"))
                            coco_annotations.append({
                                "id": ann_id,
                                "image_id": image_id,
                                "category_id": cid + 1,
                                "bbox": [box["x"], box["y"], box["w"], box["h"]],
                                "area": box["w"] * box["h"],
                                "iscrowd": 0
                            })
                            ann_id += 1

            if export_format == "yolo":
                zf.writestr("classes.txt", "\n".join(class_names))
                data_yaml = (
                    "path: .\n"
                    "train: images\n"
                    "val: images\n"
                    f"nc: {len(class_names)}\n"
                    f"names: {class_names}\n"
                )
                zf.writestr("data.yaml", data_yaml)
            else:
                coco_json = {
                    "images": coco_images,
                    "annotations": coco_annotations,
                    "categories": [
                        {"id": i + 1, "name": name} for i, name in enumerate(class_names)
                    ]
                }
                zf.writestr("annotations.json", json.dumps(coco_json, ensure_ascii=False, indent=2))

        zip_buffer.seek(0)

        resp = send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"{project}_{task_type}_{export_format}.zip"
        )
        resp.headers.add("Access-Control-Allow-Origin", "*")
        return resp

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


# =========================================================
# 🚀 TRAIN DETECTION / SEGMENTATION (YOLOv8 จริง ผ่าน Ultralytics)
# ⚠️ ต้อง `pip install ultralytics` เพิ่มใน requirements.txt ก่อน deploy
#    และเปิด "CPU is always allocated" บน Cloud Run service นี้
#    ไม่งั้น background thread จะหยุดทำงานหลัง response ถูกส่งกลับไปแล้ว
#
# รองรับเฉพาะฟอร์แมต YOLO เท่านั้น (COCO ยังคง export-only ผ่าน /export_dataset
# เพราะ COCO ไม่ได้ผูกกับ trainer ตัวไหนตัวหนึ่งโดยเฉพาะ ต่างจาก YOLO ที่
# ultralytics ใช้ฟอร์แมตนี้ตรงๆ ได้เลย)
# =========================================================
det_seg_training_status = {}   # key = f"{email}/{project}/{taskType}"


def _build_yolo_dataset_on_disk(email, project, task_type, base_dir, val_ratio=0.2):
    """
    ดาวน์โหลดรูป + แปลง annotation เป็น YOLO format วางไว้ที่ base_dir ตามโครงสร้าง
    ที่ ultralytics ต้องการ:
      base_dir/images/train, base_dir/images/val
      base_dir/labels/train, base_dir/labels/val
    คืนค่า (class_names, total_images)
    """
    is_segmentation = task_type == "segmentation"
    collection_name = "Segment" if is_segmentation else "detection"

    image_docs = list(
        worker_db.collection("user").document(email)
        .collection(collection_name).document(project)
        .collection("images").stream()
    )

    if not image_docs:
        raise ValueError("ไม่พบรูปภาพในโปรเจกต์นี้ กรุณาบันทึกรูปภาพอย่างน้อย 1 รูปก่อนเทรน")

    class_names = []

    def get_class_id(name):
        if name not in class_names:
            class_names.append(name)
        return class_names.index(name)

    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        os.makedirs(os.path.join(base_dir, sub), exist_ok=True)

    n_docs = len(image_docs)
    total = 0

    for idx, doc in enumerate(image_docs):
        d = doc.to_dict() or {}
        storage_path = d.get("storage_path")
        img_w = d.get("image_width")
        img_h = d.get("image_height")
        filename = d.get("image_filename") or f"{doc.id}.jpg"

        if not storage_path or not img_w or not img_h:
            continue

        try:
            img_bytes = bucket.blob(storage_path).download_as_bytes()
        except Exception:
            continue

        # แบ่ง train/val ง่ายๆ ตาม val_ratio ถ้าข้อมูลน้อยเกินไป (<5 ภาพ) ให้ไป train หมด
        split = "val" if (n_docs >= 5 and idx % max(2, int(1 / val_ratio)) == 0) else "train"

        with open(os.path.join(base_dir, "images", split, filename), "wb") as f:
            f.write(img_bytes)

        lines = []
        if is_segmentation:
            for poly in d.get("polygons", []):
                cid = get_class_id(poly.get("label", "object"))
                norm_pts = _yolo_normalize_polygon(poly.get("points", []), img_w, img_h)
                coords = " ".join(f"{x:.6f} {y:.6f}" for x, y in norm_pts)
                lines.append(f"{cid} {coords}")
        else:
            for box in d.get("bounding_boxes", []):
                cid = get_class_id(box.get("label", "object"))
                xc, yc, wn, hn = _yolo_normalize_bbox(box, img_w, img_h)
                lines.append(f"{cid} {xc:.6f} {yc:.6f} {wn:.6f} {hn:.6f}")

        label_filename = os.path.splitext(filename)[0] + ".txt"
        with open(os.path.join(base_dir, "labels", split, label_filename), "w") as f:
            f.write("\n".join(lines))

        total += 1

    # กันเคส val ว่างเปล่า (ข้อมูลน้อยมาก) -> copy 1 ภาพจาก train มาเป็น val แทน
    # (ultralytics ต้องการ val set เสมอ ต่อให้จะ evaluate ซ้ำกับ train ก็ตาม)
    val_dir = os.path.join(base_dir, "images", "val")
    if not os.listdir(val_dir):
        train_dir = os.path.join(base_dir, "images", "train")
        train_images = os.listdir(train_dir)
        if train_images:
            sample = train_images[0]
            shutil.copy(os.path.join(train_dir, sample), os.path.join(val_dir, sample))
            label_sample = os.path.splitext(sample)[0] + ".txt"
            shutil.copy(
                os.path.join(base_dir, "labels", "train", label_sample),
                os.path.join(base_dir, "labels", "val", label_sample)
            )

    if total == 0:
        raise ValueError("ไม่มีรูปภาพที่ข้อมูลครบสมบูรณ์พอจะใช้เทรนได้")

    return class_names, total


def run_det_seg_training(email, project, task_type):
    key = f"{email}/{project}/{task_type}"
    tmp_dir = tempfile.mkdtemp(prefix="yolo_train_")

    try:
        # import ตรงนี้ (ไม่ import ไว้บนสุดของไฟล์) เพื่อไม่ให้กระทบ startup time
        # ของ service ถ้าไม่มีใครใช้ฟีเจอร์นี้เลย
        from ultralytics import YOLO

        class_names, total_images = _build_yolo_dataset_on_disk(email, project, task_type, tmp_dir)

        data_yaml_path = os.path.join(tmp_dir, "data.yaml")
        with open(data_yaml_path, "w") as f:
            f.write(
                f"path: {tmp_dir}\n"
                f"train: images/train\n"
                f"val: images/val\n"
                f"nc: {len(class_names)}\n"
                f"names: {class_names}\n"
            )

        det_seg_training_status[key]["progress"] = 10
        det_seg_training_status[key]["total_images"] = total_images

        base_weights = "yolov8n-seg.pt" if task_type == "segmentation" else "yolov8n.pt"
        model = YOLO(base_weights)

        EPOCHS = 30  # โมเดล nano + epoch น้อย เพื่อให้พอไหวบน CPU (Cloud Run ไม่มี GPU)

        def on_epoch_end(trainer):
            epoch = trainer.epoch + 1
            pct = 10 + int((epoch / EPOCHS) * 85)
            det_seg_training_status[key]["progress"] = min(pct, 95)

        model.add_callback("on_train_epoch_end", on_epoch_end)

        model.train(
            data=data_yaml_path,
            epochs=EPOCHS,
            imgsz=640,
            project=tmp_dir,
            name="run",
            verbose=False
        )

        best_path = os.path.join(tmp_dir, "run", "weights", "best.pt")
        if not os.path.exists(best_path):
            raise RuntimeError("ไม่พบไฟล์ best.pt หลังเทรนเสร็จ")

        model_storage_path = f"{email}/{project}/model_det_seg/{task_type}/best.pt"
        bucket.blob(model_storage_path).upload_from_filename(best_path)

        labels_path = f"{email}/{project}/model_det_seg/{task_type}/classes.json"
        bucket.blob(labels_path).upload_from_string(
            json.dumps(class_names), content_type="application/json"
        )

        det_seg_training_status[key] = {
            "status": "done",
            "progress": 100,
            "classes": class_names,
            "total_images": total_images
        }

    except Exception as e:
        traceback.print_exc()
        det_seg_training_status[key] = {"status": "error", "message": str(e)}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/train_det_seg", methods=["POST", "OPTIONS"])
def train_det_seg():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    task_type = (data.get("taskType") or "detection").strip().lower()
    export_format = (data.get("format") or "yolo").strip().lower()

    if not email or not project:
        return jsonify({"success": False, "message": "Missing email or project"}), 400

    if export_format != "yolo":
        return jsonify({
            "success": False,
            "message": "รองรับการเทรนจริงเฉพาะฟอร์แมต YOLO เท่านั้น "
                       "(เลือก COCO เพื่อ Export แล้วนำไปเทรนเองภายนอกแทน)"
        }), 400

    key = f"{email}/{project}/{task_type}"
    det_seg_training_status[key] = {"status": "running", "progress": 0}

    thread = threading.Thread(target=run_det_seg_training, args=(email, project, task_type))
    thread.start()

    resp = jsonify({"success": True, "message": "Training started"})
    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp


@app.route("/train_det_seg_status", methods=["POST"])
def train_det_seg_status():
    data = request.get_json(silent=True) or {}
    task_type = (data.get("taskType") or "detection").strip().lower()
    key = f"{data.get('email')}/{data.get('project')}/{task_type}"
    resp = jsonify(det_seg_training_status.get(key, {"status": "idle"}))
    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp


@app.route("/download_det_seg_model", methods=["POST"])
def download_det_seg_model():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    task_type = (data.get("taskType") or "detection").strip().lower()

    if not email or not project:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    model_path = f"{email}/{project}/model_det_seg/{task_type}/best.pt"
    labels_path = f"{email}/{project}/model_det_seg/{task_type}/classes.json"

    model_blob = bucket.blob(model_path)
    if not model_blob.exists():
        return jsonify({"success": False, "message": "Model not found, กรุณา train ก่อน"}), 404

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("best.pt", model_blob.download_as_bytes())
        labels_blob = bucket.blob(labels_path)
        if labels_blob.exists():
            zf.writestr("classes.json", labels_blob.download_as_bytes())

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{project}_{task_type}_yolov8_model.zip"
    )


# =========================================================
# 🌐 EXPORT FOR EDGE (ONNX / TFLite / NCNN / TensorRT)
# ต่อยอดจาก best.pt ที่ train เสร็จแล้ว (model_det_seg/{task_type}/best.pt)
# ใช้ ultralytics YOLO(...).export(format=...) ซึ่งกิน CPU/เวลาพอสมควร
# จึงรันเป็น background thread + poll status เหมือน train_det_seg เดิม
#
# ⚠️ กรณีพิเศษ "engine" (TensorRT):
#    TensorRT engine ต้อง build บน GPU สถาปัตยกรรมเดียวกับปลายทางจริงเท่านั้น
#    (build ข้ามเครื่องไม่ได้) และ Cloud Run ไม่มี GPU เลย จึงทำได้แค่
#    export เป็น ONNX ที่นี่ แล้วแนบสคริปต์ trtexec ให้ผู้ใช้ไป build
#    .engine เองบนบอร์ด Jetson จริงตอนติดตั้งหน้างาน
# =========================================================

edge_export_status = {}   # key = f"{email}/{project}/{task_type}/{edge_format}"

VALID_EDGE_FORMATS = ("onnx", "tflite", "ncnn", "engine")

# format string ที่ ultralytics .export() ต้องการ ต่างจากชื่อที่โชว์ผู้ใช้เล็กน้อย
# หมายเหตุ: "engine" ไม่ต้องมีใน map นี้ เพราะจัดการ special case แยกใน
# run_edge_export() แล้ว (export เป็น onnx แทนเสมอ)
EDGE_FORMAT_MAP = {
    "onnx": "onnx",
    "tflite": "tflite",
    "ncnn": "ncnn",
}

# นามสกุลไฟล์ผลลัพธ์หลักที่ ultralytics สร้างให้ต่อ format
# (tflite และ ncnn จริงๆ ได้ "โฟลเดอร์" ออกมา ไม่ใช่ไฟล์เดี่ยว จึงต้อง zip ทั้งโฟลเดอร์)
EDGE_FORMAT_IS_DIR = {
    "onnx": False,
    "tflite": True,   # ultralytics สร้างเป็นโฟลเดอร์ {name}_saved_model/ พร้อม .tflite ข้างใน
    "ncnn": True,      # ultralytics สร้างเป็นโฟลเดอร์ {name}_ncnn_model/
}


def _build_trt_zip(zf, exported_onnx_path, project, task_type):
    """
    สร้างเนื้อหา zip สำหรับกรณี edge_format == "engine":
    แนบ model.onnx + สคริปต์ build_engine.sh + README.txt
    (ไม่ build .engine จริงที่นี่ เพราะ Cloud Run ไม่มี GPU)
    """
    zf.write(exported_onnx_path, "model.onnx")

    build_script = f"""#!/bin/bash
# ==========================================================
# สคริปต์ build TensorRT engine จาก ONNX
# ⚠️ ต้องรันบนบอร์ด Jetson จริง (Orin Nano/NX/AGX) ที่จะ deploy เท่านั้น
#    ห้าม build บนเครื่องอื่นแล้วก๊อปปี้ .engine ไปใช้ข้ามบอร์ด
#    ต้องติดตั้ง JetPack SDK (มี trtexec ติดมาให้อยู่แล้ว) ก่อนรัน
# ==========================================================

trtexec --onnx=model.onnx \\
        --saveEngine=model.engine \\
        --fp16 \\
        --workspace=4096

echo "✅ Build เสร็จแล้ว: model.engine"
echo "นำไปใช้กับ project: {project} (task: {task_type})"
"""
    zf.writestr("build_engine.sh", build_script)

    readme = """# วิธีใช้งาน

1. คัดลอกไฟล์ model.onnx และ build_engine.sh ไปไว้บนบอร์ด Jetson ที่จะ deploy จริง
2. รันคำสั่ง: chmod +x build_engine.sh && ./build_engine.sh
3. จะได้ไฟล์ model.engine สำหรับใช้งานบนบอร์ดนั้นโดยเฉพาะ
   (ห้ามนำ .engine ไปใช้ข้ามบอร์ดรุ่นอื่น ต้อง build ใหม่ทุกครั้งที่เปลี่ยนบอร์ด)
"""
    zf.writestr("README.txt", readme)


def run_edge_export(email, project, task_type, edge_format):
    key = f"{email}/{project}/{task_type}/{edge_format}"
    tmp_dir = tempfile.mkdtemp(prefix="edge_export_")

    try:
        from ultralytics import YOLO

        edge_export_status[key]["progress"] = 10

        # ----------------------------------------------------
        # 1) ดาวน์โหลด best.pt จาก Storage มาไว้ที่ tmp_dir ก่อน
        # ----------------------------------------------------
        model_storage_path = f"{email}/{project}/model_det_seg/{task_type}/best.pt"
        model_blob = bucket.blob(model_storage_path)

        if not model_blob.exists():
            raise ValueError("ไม่พบ best.pt กรุณา train โมเดลให้เสร็จก่อน")

        local_pt_path = os.path.join(tmp_dir, "best.pt")
        model_blob.download_to_filename(local_pt_path)

        edge_export_status[key]["progress"] = 30

        # ----------------------------------------------------
        # 2) โหลดโมเดลแล้วสั่ง export
        #    - engine -> export เป็น onnx แทน (ดู comment ด้านบนไฟล์)
        #    - อื่นๆ  -> export ตาม format จริง
        # ----------------------------------------------------
        model = YOLO(local_pt_path)

        if edge_format == "engine":
            export_kwargs = {"format": "onnx", "imgsz": 640}
        else:
            export_kwargs = {"format": EDGE_FORMAT_MAP[edge_format], "imgsz": 640}

        exported_path = model.export(**export_kwargs)
        # exported_path คือ path (str) ของไฟล์หรือโฟลเดอร์หลักที่ export ออกมา
        # อยู่ข้างๆ best.pt ใน tmp_dir เดียวกัน

        edge_export_status[key]["progress"] = 80

        # ----------------------------------------------------
        # 3) Zip ผลลัพธ์ทั้งหมด (ไฟล์เดี่ยว/โฟลเดอร์/ONNX+script) แล้วอัปโหลดขึ้น Storage
        # ----------------------------------------------------
        zip_local_path = os.path.join(tmp_dir, f"edge_{edge_format}.zip")

        with zipfile.ZipFile(zip_local_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if edge_format == "engine":
                _build_trt_zip(zf, exported_path, project, task_type)
            elif EDGE_FORMAT_IS_DIR.get(edge_format) and os.path.isdir(exported_path):
                for root, _, files in os.walk(exported_path):
                    for fname in files:
                        local_file = os.path.join(root, fname)
                        arcname = os.path.relpath(local_file, tmp_dir)
                        zf.write(local_file, arcname)
            else:
                zf.write(exported_path, os.path.basename(exported_path))

            # แนบ classes.json เดิมไปด้วยทุก format เพราะฝั่ง edge ต้องใช้ mapping class เดียวกัน
            labels_path = f"{email}/{project}/model_det_seg/{task_type}/classes.json"
            labels_blob = bucket.blob(labels_path)
            if labels_blob.exists():
                zf.writestr("classes.json", labels_blob.download_as_bytes())

        edge_storage_path = f"{email}/{project}/model_edge/{task_type}/{edge_format}.zip"
        bucket.blob(edge_storage_path).upload_from_filename(zip_local_path)

        edge_export_status[key] = {
            "status": "done",
            "progress": 100,
            "format": edge_format
        }

    except Exception as e:
        traceback.print_exc()
        edge_export_status[key] = {"status": "error", "message": str(e)}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/export_edge_format", methods=["POST", "OPTIONS"])
def export_edge_format():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    task_type = (data.get("taskType") or "detection").strip().lower()
    edge_format = (data.get("edgeFormat") or "onnx").strip().lower()

    if not email or not project:
        return jsonify({"success": False, "message": "Missing email or project"}), 400

    if edge_format not in VALID_EDGE_FORMATS:
        return jsonify({
            "success": False,
            "message": f"edgeFormat must be one of {VALID_EDGE_FORMATS}"
        }), 400

    # เช็คว่ามี best.pt อยู่จริงก่อนเริ่ม thread (ตอบ error ทันที ไม่ต้องรอ poll)
    model_storage_path = f"{email}/{project}/model_det_seg/{task_type}/best.pt"
    if not bucket.blob(model_storage_path).exists():
        return jsonify({
            "success": False,
            "message": "ไม่พบโมเดลที่ train แล้ว กรุณา train ให้เสร็จก่อน"
        }), 404

    key = f"{email}/{project}/{task_type}/{edge_format}"
    edge_export_status[key] = {"status": "running", "progress": 0}

    thread = threading.Thread(
        target=run_edge_export,
        args=(email, project, task_type, edge_format)
    )
    thread.start()

    resp = jsonify({"success": True, "message": "Edge export started"})
    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp


@app.route("/export_edge_status", methods=["POST"])
def export_edge_status():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    task_type = (data.get("taskType") or "detection").strip().lower()
    edge_format = (data.get("edgeFormat") or "onnx").strip().lower()

    key = f"{email}/{project}/{task_type}/{edge_format}"
    resp = jsonify(edge_export_status.get(key, {"status": "idle"}))
    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp


@app.route("/download_edge_model", methods=["POST"])
def download_edge_model():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    task_type = (data.get("taskType") or "detection").strip().lower()
    edge_format = (data.get("edgeFormat") or "onnx").strip().lower()

    if not email or not project or edge_format not in VALID_EDGE_FORMATS:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    edge_storage_path = f"{email}/{project}/model_edge/{task_type}/{edge_format}.zip"
    blob = bucket.blob(edge_storage_path)

    if not blob.exists():
        return jsonify({
            "success": False,
            "message": "ยังไม่มีไฟล์ export กรุณา export ก่อน"
        }), 404

    return send_file(
        io.BytesIO(blob.download_as_bytes()),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{project}_{task_type}_{edge_format}.zip"
    )

# =========================================================
# DELETE PROJECT
# ลบทั้งโปรเจกต์:
#   1) Storage   : ลบไฟล์ทุกไฟล์ใต้ path  {email}/{project}/...
#                  เช่น /enthongsri@gmail.com/capcolor/...
#   2) Firestore : ลบ document ที่ path
#                  user/{email}/dataset_session/{project}
#
# วางต่อท้ายไฟล์ app.py เดิม (ใช้ตัวแปร bucket / worker_db
# ที่ประกาศไว้แล้วด้านบนของไฟล์ ไม่ต้อง import เพิ่ม)
# =========================================================
@app.route("/delete_project", methods=["POST"])
def delete_project():
    try:
        data = request.get_json(force=True) or {}

        email = data.get("email")
        project = data.get("project")

        if not email or not project:
            return jsonify({
                "status": "error",
                "message": "Missing email or project"
            }), 400

        # ---------------------------------------------------
        # 1) ลบไฟล์ทั้งหมดใน Storage ใต้ path {email}/{project}/
        # ---------------------------------------------------
        prefix = f"{email}/{project}/"

        blobs = list(bucket.list_blobs(prefix=prefix))

        for blob in blobs:
            blob.delete()

        # ---------------------------------------------------
        # 2) ลบ Firestore document:
        #    user/{email}/dataset_session/{project}
        # ---------------------------------------------------
        doc_ref = (
            worker_db
            .collection("user")
            .document(email)
            .collection("dataset_session")
            .document(project)
        )

        doc_ref.delete()

        return jsonify({
            "status": "ok",
            "deleted_files": len(blobs)
        })

    except Exception as e:
        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
#===========================================================
@app.route("/list_servers", methods=["GET"])
@require_admin_key
def list_servers():
    try:
        servers = []

        docs = (
            hub_db.collection("hub_system")
            .document("server_pool")
            .collection("servers")
            .stream()
        )

        now = int(time.time())

        for doc in docs:
            data = doc.to_dict() or {}
            last_heartbeat = data.get("last_heartbeat", 0)

            # ไม่มี heartbeat เข้ามาเกิน 90 วิ ถือว่า offline
            is_online = (now - last_heartbeat) <= 90

            servers.append({
                "server_id": doc.id,
                "cloud_url": data.get("cloud_url", ""),
                "status": "online" if is_online else "offline",
                "active_users": data.get("active_users", 0),
                "load_score": data.get("load_score", 0),
                "last_heartbeat": last_heartbeat
            })

        servers.sort(key=lambda s: s["server_id"])

        return jsonify({
            "status": "success",
            "servers": servers
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
 #=======================================  
# =========================================================
# ENDPOINT: รับแจ้งชำระเงิน และบันทึกลง FIREBASE (UPDATE TYPE)
# =========================================================
@app.route("/api/payment/confirm", methods=["POST"])
def confirm_payment():
    try:
        # 1. ดึงข้อมูล Text จาก FormData
        email = request.form.get("email")
        amount = request.form.get("amount")
        bank = request.form.get("bank")
        transfer_time = request.form.get("transfer_time")

        # ตรวจสอบค่าห้ามว่าง
        if not email or not amount or not bank or not transfer_time:
            return jsonify({"error": "Missing required text fields"}), 400

        # 2. ดึงไฟล์รูปภาพสลิป
        if "slip" not in request.files:
            return jsonify({"error": "Missing slip image file"}), 400
        
        file = request.files["slip"]
        if not file or file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        # ป้องกัน Pylance แจ้งเตือนเรื่อง Type "str | None"
        filename = file.filename if file.filename is not None else "slip.jpg"
        file_ext = os.path.splitext(filename)[1] or ".jpg"
        unique_filename = f"{uuid.uuid4()}{file_ext}"

        # 3. อัปโหลดรูปสลิปขึ้น Firebase Storage (worker_app)
        # ปลายทาง path -> /{email}/"payment"/{filename}
        storage_path = f"{email}/payment/{unique_filename}"
        blob = bucket.blob(storage_path)
        
        # อ่านไฟล์และกำหนด Content-Type แบบปลอดภัยจาก None
        file_stream = file.read()
        content_type = file.content_type if file.content_type is not None else "image/jpeg"
        
        blob.upload_from_string(file_stream, content_type=content_type)
        
        # ทำการสิทธิ์เปิดดูรูปภาพผ่านลิงก์สาธารณะ
        blob.make_public()
        slip_url = blob.public_url

        # 4. บันทึกข้อมูลอื่นๆ ลง Firestore (worker_db)
        # ปลายทาง path -> /user/{email}/"payment"/"data"/records/{random_id}
        payment_data = {
            "amount": float(amount),
            "bank": bank,
            "transfer_time": transfer_time,
            "slip_url": slip_url,
            "storage_path": storage_path,
            "created_at": firestore.SERVER_TIMESTAMP,
            "status": "pending"  # ตั้งสถานะเริ่มต้นรอการตรวจสอบ
        }
       #doc_ref = worker_db.collection("user").document(email).collection("payment").document("data").collection("records").document()
        doc_ref = worker_db.collection("user").document(email).collection("payment").document()
        doc_ref.set(payment_data)

        return jsonify({
            "message": "Payment confirmation submitted successfully",
            "doc_id": doc_ref.id,
            "slip_url": slip_url
        }), 200

    except Exception as e:
        print("--- PAYMENT SUBMIT ERROR ---")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500     
# =========================================================
# LIST CLASS IMAGES
#   Storage path: {email}/{project}/class/{className}/...
#   ส่งกลับรูปแบบ pagination รอบละ `limit` รูป (default 50)
#
# วางต่อท้ายไฟล์ app.py เดิม (ใช้ bucket ที่ประกาศไว้แล้ว
# ไม่ต้อง import เพิ่ม เพราะ timedelta ถูก import ไว้แล้วด้านบน)
# =========================================================
@app.route("/list_class_images", methods=["POST"])
def list_class_images():
    try:
        data = request.get_json(force=True) or {}

        email = data.get("email")
        project = data.get("project")
        class_name = data.get("className")
        offset = int(data.get("offset", 0))
        limit = int(data.get("limit", 50))

        if not email or not project or not class_name:
            return jsonify({
                "status": "error",
                "message": "Missing email, project or className"
            }), 400

        prefix = f"{email}/{project}/class/{class_name}/"

        all_blobs = list(bucket.list_blobs(prefix=prefix))
        all_blobs.sort(key=lambda b: b.name)

        page_blobs = all_blobs[offset: offset + limit]

        images = []

        for blob in page_blobs:
            file_name = blob.name.split("/")[-1]

            # signed url ใช้แสดงรูปได้ชั่วคราว (2 ชม.) โดยไม่ต้องเปิด public bucket
            url = blob.generate_signed_url(
                expiration=timedelta(hours=2)
            )

            images.append({
                "name": file_name,
                "url": url
            })

        has_more = (offset + limit) < len(all_blobs)

        return jsonify({
            "status": "ok",
            "images": images,
            "total": len(all_blobs),
            "hasMore": has_more
        })

    except Exception as e:
        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# =========================================================
# DELETE SINGLE IMAGE
#   Storage  : {email}/{project}/class/{className}/{fileName}
#   Firestore: {email}/{project}/class/{className}
#              (ลด total_images -1 และตัดชื่อไฟล์ออกจาก
#               field "images" ถ้ามีการเก็บ array ไว้)
# =========================================================
@app.route("/delete_image", methods=["POST"])
def delete_image():
    try:
        data = request.get_json(force=True) or {}

        email = data.get("email")
        project = data.get("project")
        class_name = data.get("className")
        file_name = data.get("fileName")

        if not email or not project or not class_name or not file_name:
            return jsonify({
                "status": "error",
                "message": "Missing email, project, className or fileName"
            }), 400

        # ---------------------------------------------------
        # 1) ลบไฟล์รูปจาก Storage
        # ---------------------------------------------------
        blob_path = f"{email}/{project}/class/{class_name}/{file_name}"
        blob = bucket.blob(blob_path)

        if blob.exists():
            blob.delete()

        # ---------------------------------------------------
        # 2) อัปเดต Firestore document ของ class นี้
        #    path: {email}/{project}/class/{className}
        #    (ไม่ให้ error ตรงนี้ทำให้ทั้ง request fail
        #     เพราะไฟล์ถูกลบออกจาก Storage สำเร็จไปแล้ว)
        # ---------------------------------------------------
        try:
            class_ref = (
                worker_db
                .collection("user")
                .document(email)
                .collection("dataset_session")
                .document(project)
                .collection("class")
                .document(class_name)
            )

            # ลบ document รูปนี้ออกจาก subcollection images (สมมติ doc id = fileName)
            class_ref.collection("images").document(file_name).delete()

            # ลด total_images บน class doc เอง (ถ้ามี field นี้เก็บอยู่)
            class_ref.update({
                "total_images": firestore.Increment(-1)
            })

        except Exception:
            traceback.print_exc()

        return jsonify({
            "status": "ok"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500  
#===================keep dataset =====================================     
try:
    RESAMPLE_BICUBIC = Image.Resampling.BICUBIC
except AttributeError:
    RESAMPLE_BICUBIC = getattr(Image, "BICUBIC")



# โหมดที่ยังต้องให้ backend แปลงภาพจริง (กระทบตำแหน่ง annotation จึงต้องคำนวณใหม่ด้วย)
GEOMETRIC_MODES = {"rotation_-10", "rotation_10", "zoom_in", "flip_horizontal"}

# โหมดที่ฝั่ง React แปลงภาพจริงมาก่อนส่งแล้ว (ไม่กระทบตำแหน่ง annotation)
PIXEL_LEVEL_MODES = {
    "grayscale", "blur",
    "brightness_dark", "brightness_bright",
    "contrast_low", "contrast_high",
    "saturation_low", "saturation_high",
}

# 🌟 ประเภทงานที่รองรับ -> ใช้เลือกทั้งชื่อ collection ใน Firestore และโฟลเดอร์ใน Storage
# "detection"    -> /user/{email}/detection/{project}/images/{doc_id}   , storage: {email}/Detection/{project}/...
# "segmentation" -> /user/{email}/Segment/{project}/images/{doc_id}    , storage: {email}/Segment/{project}/...
PROJECT_TYPE_CONFIG = {
    "detection": {
        "collection": "detection",
        "storage_folder": "Detection",
    },
    "segmentation": {
        "collection": "Segment",
        "storage_folder": "Segment",
    },
}


# ==========================================================
# 🟧 Bounding Box helpers (Detection)
# ==========================================================
def normalize_boxes_to_image_space(boxes, canvas_w, canvas_h, img_w, img_h):
    """
    กล่องที่ React ส่งมาอ้างอิงพิกัดพิกเซลของ container (object-fit: contain)
    ซึ่งถ้าอัตราส่วนภาพกับ container ไม่เท่ากัน จะมีแถบว่าง (letterbox) ซ้าย-ขวา หรือ บน-ล่าง
    ฟังก์ชันนี้แปลงกลับเป็นพิกัดจริงบนไฟล์ภาพ (หน่วยพิกเซลของภาพจริง)
    """
    if not canvas_w or not canvas_h or canvas_w <= 0 or canvas_h <= 0:
        # ไม่มีข้อมูล container ให้ fallback คืนพิกัดเดิม (เผื่อ client เก่าไม่ส่งมา)
        return boxes

    scale = min(canvas_w / img_w, canvas_h / img_h)
    displayed_w = img_w * scale
    displayed_h = img_h * scale
    offset_x = (canvas_w - displayed_w) / 2
    offset_y = (canvas_h - displayed_h) / 2

    normalized = []
    for b in boxes:
        x = (b["x"] - offset_x) / scale
        y = (b["y"] - offset_y) / scale
        w = b["w"] / scale
        h = b["h"] / scale

        # จำกัดไม่ให้กล่องหลุดขอบภาพจริง (เผื่อผู้ใช้วาดชิดขอบ container)
        x = max(0, min(x, img_w))
        y = max(0, min(y, img_h))
        w = max(0, min(w, img_w - x))
        h = max(0, min(h, img_h - y))

        normalized.append({
            "label": b["label"],
            "x": round(x), "y": round(y),
            "w": round(w), "h": round(h),
        })
    return normalized


def _rotate_boxes(boxes, clockwise_deg, img_w, img_h):
    """
    หมุนกล่องรอบจุดกึ่งกลางภาพตามมุม clockwise_deg (องศา, ค่าบวก = หมุนตามเข็มนาฬิกา
    เหมือนที่ React preview ใช้ CSS `rotate(Ndeg)`) แล้วคำนวณ axis-aligned bounding box ใหม่
    จากมุมทั้ง 4 ของกล่องเดิมที่หมุนแล้ว
    """
    angle = math.radians(clockwise_deg)
    cx, cy = img_w / 2, img_h / 2
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    result = []
    for b in boxes:
        corners = [
            (b["x"], b["y"]),
            (b["x"] + b["w"], b["y"]),
            (b["x"], b["y"] + b["h"]),
            (b["x"] + b["w"], b["y"] + b["h"]),
        ]
        rotated = []
        for px, py in corners:
            dx, dy = px - cx, py - cy
            rx = cx + dx * cos_a - dy * sin_a
            ry = cy + dx * sin_a + dy * cos_a
            rotated.append((rx, ry))

        xs = [p[0] for p in rotated]
        ys = [p[1] for p in rotated]
        nx = max(0, min(xs))
        ny = max(0, min(ys))
        nw = min(img_w, max(xs)) - nx
        nh = min(img_h, max(ys)) - ny

        result.append({
            "label": b["label"],
            "x": round(nx), "y": round(ny),
            "w": round(max(0, nw)), "h": round(max(0, nh)),
        })
    return result


def _zoom_boxes(boxes, zoom_factor, img_w, img_h):
    """ขยายกล่องรอบจุดกึ่งกลางภาพตาม zoom_factor เดียวกับที่ใช้ crop+resize ภาพ"""
    cx, cy = img_w / 2, img_h / 2
    result = []
    for b in boxes:
        x = cx + (b["x"] - cx) * zoom_factor
        y = cy + (b["y"] - cy) * zoom_factor
        w = b["w"] * zoom_factor
        h = b["h"] * zoom_factor

        x = max(0, min(x, img_w))
        y = max(0, min(y, img_h))
        w = max(0, min(w, img_w - x))
        h = max(0, min(h, img_h - y))

        result.append({
            "label": b["label"],
            "x": round(x), "y": round(y),
            "w": round(w), "h": round(h),
        })
    return result


def _flip_boxes_horizontal(boxes, img_w):
    """พลิกกล่องแนวนอนตามภาพที่ mirror แล้ว"""
    result = []
    for b in boxes:
        new_x = img_w - (b["x"] + b["w"])
        result.append({
            "label": b["label"],
            "x": round(new_x), "y": round(b["y"]),
            "w": round(b["w"]), "h": round(b["h"]),
        })
    return result


def apply_geometric_augmentation(img, aug_mode, boxes):
    """
    แปลงภาพจริง (PIL Image) ตาม aug_mode ที่เป็น geometric แล้วคืนกล่องที่คำนวณใหม่ให้ตรงกับภาพ
    คืนค่า (augmented_img, augmented_boxes)
    """
    img_w, img_h = img.size

    if aug_mode == "rotation_-10":
        # ต้องการหมุนภาพ 10 องศาทวนเข็มนาฬิกา (ตรงกับ CSS rotate(-10deg) ฝั่ง preview)
        # PIL.rotate() มุมบวก = ทวนเข็มนาฬิกาอยู่แล้ว จึงใส่ 10 ตรงๆ
        new_img = img.rotate(10, resample=RESAMPLE_BICUBIC, expand=False, fillcolor=(0, 0, 0))
        new_boxes = _rotate_boxes(boxes, clockwise_deg=-10, img_w=img_w, img_h=img_h)
        return new_img, new_boxes

    if aug_mode == "rotation_10":
        # หมุนภาพ 10 องศาตามเข็มนาฬิกา (ตรงกับ CSS rotate(10deg))
        # PIL.rotate() มุมบวกทวนเข็ม จึงต้องใส่ค่าติดลบ
        new_img = img.rotate(-10, resample=RESAMPLE_BICUBIC, expand=False, fillcolor=(0, 0, 0))
        new_boxes = _rotate_boxes(boxes, clockwise_deg=10, img_w=img_w, img_h=img_h)
        return new_img, new_boxes

    if aug_mode == "zoom_in":
        zoom = 1.15
        crop_w, crop_h = int(img_w / zoom), int(img_h / zoom)
        left = (img_w - crop_w) // 2
        top = (img_h - crop_h) // 2
        cropped = img.crop((left, top, left + crop_w, top + crop_h))
        new_img = cropped.resize((img_w, img_h), RESAMPLE_BICUBIC)
        new_boxes = _zoom_boxes(boxes, zoom_factor=zoom, img_w=img_w, img_h=img_h)
        return new_img, new_boxes

    if aug_mode == "flip_horizontal":
        new_img = ImageOps.mirror(img)
        new_boxes = _flip_boxes_horizontal(boxes, img_w=img_w)
        return new_img, new_boxes

    return img, boxes


# ==========================================================
# ⬡ Polygon helpers (Segmentation)
# แนวคิดเดียวกับ Bounding Box แต่ทำงานกับ "จุด" แต่ละจุดของ polygon โดยตรง
# (ไม่ต้องคำนวณ axis-aligned bounding box กลับ เหมือนกรณี Detection)
# ==========================================================
def normalize_polygons_to_image_space(polygons, canvas_w, canvas_h, img_w, img_h):
    """แปลงพิกัดจุดของทุก polygon จาก container space (มี letterbox) -> พิกัดจริงบนภาพ"""
    if not canvas_w or not canvas_h or canvas_w <= 0 or canvas_h <= 0:
        return polygons

    scale = min(canvas_w / img_w, canvas_h / img_h)
    displayed_w = img_w * scale
    displayed_h = img_h * scale
    offset_x = (canvas_w - displayed_w) / 2
    offset_y = (canvas_h - displayed_h) / 2

    normalized = []
    for poly in polygons:
        pts = []
        for p in poly.get("points", []):
            x = (p["x"] - offset_x) / scale
            y = (p["y"] - offset_y) / scale
            x = max(0, min(x, img_w))
            y = max(0, min(y, img_h))
            pts.append({"x": round(x), "y": round(y)})
        normalized.append({"label": poly.get("label", ""), "points": pts})
    return normalized


def _rotate_points(points, clockwise_deg, img_w, img_h):
    angle = math.radians(clockwise_deg)
    cx, cy = img_w / 2, img_h / 2
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    result = []
    for p in points:
        dx, dy = p["x"] - cx, p["y"] - cy
        rx = cx + dx * cos_a - dy * sin_a
        ry = cy + dx * sin_a + dy * cos_a
        result.append({
            "x": round(max(0, min(rx, img_w))),
            "y": round(max(0, min(ry, img_h))),
        })
    return result


def _zoom_points(points, zoom_factor, img_w, img_h):
    cx, cy = img_w / 2, img_h / 2
    result = []
    for p in points:
        x = cx + (p["x"] - cx) * zoom_factor
        y = cy + (p["y"] - cy) * zoom_factor
        result.append({
            "x": round(max(0, min(x, img_w))),
            "y": round(max(0, min(y, img_h))),
        })
    return result


def _flip_points_horizontal(points, img_w):
    return [{"x": round(img_w - p["x"]), "y": round(p["y"])} for p in points]


def apply_geometric_augmentation_to_polygons(aug_mode, polygons, img_w, img_h):
    """
    ใช้ transform เดียวกับที่ apply_geometric_augmentation ทำกับภาพ แต่ทำกับจุด polygon โดยตรง
    คืนค่า polygons ใหม่ (list ของ {label, points})
    """
    if aug_mode == "rotation_-10":
        return [{"label": poly["label"], "points": _rotate_points(poly["points"], -10, img_w, img_h)} for poly in polygons]
    if aug_mode == "rotation_10":
        return [{"label": poly["label"], "points": _rotate_points(poly["points"], 10, img_w, img_h)} for poly in polygons]
    if aug_mode == "zoom_in":
        return [{"label": poly["label"], "points": _zoom_points(poly["points"], 1.15, img_w, img_h)} for poly in polygons]
    if aug_mode == "flip_horizontal":
        return [{"label": poly["label"], "points": _flip_points_horizontal(poly["points"], img_w)} for poly in polygons]
    return polygons


def _cors(resp):
    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp


@app.route("/api/upload_dataset", methods=["POST", "OPTIONS"])
def upload_dataset():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    try:
        data = request.json
        if not data:
            return _cors(jsonify({"error": "Missing request body"})), 400

        email = data.get("email")
        project = data.get("project_name")
        class_name = data.get("class_name")
        aug_mode = data.get("aug_mode", "original")
        image_data = data.get("image_data")  # Base64 string
        canvas_width = data.get("canvas_width")
        canvas_height = data.get("canvas_height")

        # 🌟 ประเภทงาน: "detection" (default) หรือ "segmentation"
        # ใช้เลือก Firestore collection + Storage folder + รูปแบบ annotation ที่จะประมวลผล
        project_type = (data.get("project_type") or "detection").strip().lower()
        type_config = PROJECT_TYPE_CONFIG.get(project_type, PROJECT_TYPE_CONFIG["detection"])
        collection_name = type_config["collection"]
        storage_folder = type_config["storage_folder"]
        is_segmentation = project_type == "segmentation"

        if not all([email, project, class_name, image_data]):
            return _cors(jsonify({
                "error": "Missing required fields (email, project_name, class_name, image_data)"
            })), 400

        # ✅ ตรวจสอบข้อมูล annotation ตามประเภทงาน
        if is_segmentation:
            raw_polygons = data.get("polygons", [])
            if not raw_polygons:
                return _cors(jsonify({"error": "Missing polygons data for segmentation"})), 400
        else:
            raw_boxes = data.get("bounding_boxes", [])

        # -------------------------------------------------- 
        # 1. Decode base64 -> PIL Image (แปลงเป็น RGB เผื่อ PNG มี alpha channel)
        # ---------------------------------------------------------
        if "," in image_data:
            _, base64_str = image_data.split(",", 1)
        else:
            base64_str = image_data

        image_bytes_in = base64.b64decode(base64_str)
        pil_img = Image.open(io.BytesIO(image_bytes_in)).convert("RGB")
        img_w, img_h = pil_img.size

        # ---------------------------------------------------------
        # 2. Normalize พิกัด annotation จาก container space -> image pixel space
        #    (แก้ปัญหา letterboxing จาก object-fit: contain)
        # ---------------------------------------------------------
        if is_segmentation:
            normalized_polygons = normalize_polygons_to_image_space(
                raw_polygons, canvas_width, canvas_height, img_w, img_h
            )
        else:
            normalized_boxes = normalize_boxes_to_image_space(
                raw_boxes, canvas_width, canvas_height, img_w, img_h
            )

        # ---------------------------------------------------------
        # 3. ถ้าเป็นโหมด geometric ให้แปลงภาพจริง + คำนวณ annotation ใหม่ตามภาพ
        #    ถ้าเป็นโหมด pixel-level ฝั่ง React แปลงภาพมาก่อนส่งแล้ว ข้ามขั้นตอนนี้
        # ---------------------------------------------------------
        if aug_mode in GEOMETRIC_MODES:
            if is_segmentation:
                # ใช้ apply_geometric_augmentation แปลงตัวภาพเฉยๆ (ไม่สนใจ boxes ที่คืนมา)
                pil_img, _ = apply_geometric_augmentation(pil_img, aug_mode, [])
                final_polygons = apply_geometric_augmentation_to_polygons(
                    aug_mode, normalized_polygons, img_w, img_h
                )
            else:
                pil_img, final_boxes = apply_geometric_augmentation(pil_img, aug_mode, normalized_boxes)
        else:
            if is_segmentation:
                final_polygons = normalized_polygons
            else:
                final_boxes = normalized_boxes

        # ---------------------------------------------------------
        # 4. Encode ภาพที่ได้ (ต้นฉบับ หรือแปลงแล้ว) กลับเป็น JPEG bytes
        # ---------------------------------------------------------
        out_buffer = io.BytesIO()
        pil_img.save(out_buffer, format="JPEG", quality=92)
        image_bytes_out = out_buffer.getvalue()

        # ---------------------------------------------------------
        # 5. อัปโหลดขึ้น Firebase Storage
        #    path: /{email}/{storage_folder}/{project}/{filename}
        #    - detection    -> {email}/Detection/{project}/...
        #    - segmentation -> {email}/Segment/{project}/...
        # ---------------------------------------------------------
        filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
        storage_path = f"{email}/{storage_folder}/{project}/{filename}"

        blob = bucket.blob(storage_path)
        blob.upload_from_string(image_bytes_out, content_type="image/jpeg")
        blob.make_public()
        image_url = blob.public_url

        # ---------------------------------------------------------
        # 6. บันทึกข้อมูลลง Firestore
        #    - detection    -> /user/{email}/detection/{project}/images/{doc_id}
        #    - segmentation -> /user/{email}/Segment/{project}/images/{doc_id}
        # ---------------------------------------------------------
        doc_id = filename.split(".")[0]

        doc_ref = worker_db.collection("user").document(email) \
                           .collection(collection_name).document(project) \
                           .collection("images").document(doc_id)

        firestore_payload = {
            "class_name": class_name,
            "project_type": project_type,
            "annotation_type": "polygon" if is_segmentation else "bbox",
            "image_filename": filename,
            "storage_path": storage_path,
            "image_url": image_url,
            "aug_mode": aug_mode,
            "image_width": img_w,
            "image_height": img_h,
            "canvas_width_at_capture": canvas_width,
            "canvas_height_at_capture": canvas_height,
            "timestamp": datetime.utcnow(),
        }

        if is_segmentation:
            firestore_payload["polygons"] = final_polygons
        else:
            firestore_payload["bounding_boxes"] = final_boxes

        doc_ref.set(firestore_payload)

        # ---------------------------------------------------------
        # 7. อัปเดตสรุปยอดรวมของ project
        #    - detection    -> /user/{email}/detection/{project}
        #    - segmentation -> /user/{email}/Segment/{project}
        #    เพื่อให้ endpoint อื่น (เช่น /get_detection_projects, /get_segmentation_projects)
        #    อ่าน total_images ถูกต้อง
        # ---------------------------------------------------------
        project_summary_ref = worker_db.collection("user").document(email) \
                                        .collection(collection_name).document(project)
        project_summary_ref.set({
            "project": project,
            "total_images": firestore.Increment(1),
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)

        response_payload = {
            "success": True,
            "message": "Dataset saved successfully",
            "project_type": project_type,
            "storage_path": storage_path,
            "image_url": image_url,
        }
        if is_segmentation:
            response_payload["polygons"] = final_polygons
        else:
            response_payload["bounding_boxes"] = final_boxes

        return _cors(jsonify(response_payload)), 200

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return _cors(jsonify({"error": str(e)})), 500
# =========================================================
# UPDATE PROJECT TYPE (เพิ่มใหม่เพื่อให้ React เรียกใช้ได้)
# =========================================================
@app.route("/update_project_type", methods=["POST", "OPTIONS"])
def update_project_type():
    # จัดการกรณี Preflight request จากบราวเซอร์ (CORS OPTIONS)
    if request.method == "OPTIONS":
        return jsonify({"success": True}), 200

    try:
        data = request.get_json(silent=True) or {}
        
        email = data.get("email", "").lower().strip()
        project_name = data.get("project", "").strip()
        project_type = data.get("projectType", "").strip() # รับค่าประเภท เช่น 'Premium', 'Standard'

        # 1. Validation เช็คความถูกต้องของข้อมูล
        if not email or not project_name or not project_type:
            return jsonify({
                "success": False,
                "message": "Missing email, project, or projectType"
            }), 400

        # 2. ค้นหาเอกสารอ้างอิงโปรเจกต์ในคอลเลกชัน dataset_session
        project_ref = (
            worker_db
            .collection("user")
            .document(email)
            .collection("dataset_session")
            .document(project_name)
        )

        # ตรวจสอบว่ามีโปรเจกต์นี้อยู่จริงไหม
        if not project_ref.get().exists:
            return jsonify({
                "success": False,
                "message": f"Project '{project_name}' not found."
            }), 404

        # 3. อัปเดตข้อมูลประเภทโปรเจกต์เข้าไปใน Firestore
        project_ref.update({
            "projectType": project_type,
            "updated_at": firestore.SERVER_TIMESTAMP
        })

        print(f"✅ Project '{project_name}' of {email} updated to {project_type}")

        return jsonify({
            "success": True,
            "message": "Project type updated successfully",
            "project": project_name,
            "projectType": project_type
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
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