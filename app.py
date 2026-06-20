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

from datetime import timedelta
 

import tensorflow as tf 
# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)
 
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
print(input_details)
print(output_details)
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
@app.route("/predict", methods=["POST"])
def predict():

    try:

        data = request.get_json()

        image_b64 = data["image"]

        image_bytes = base64.b64decode(
            image_b64
        )

        image = Image.open(
            BytesIO(image_bytes)
        ).convert("RGB")

        # Resize ตามโมเดล
        image = image.resize((224, 224))

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

        prediction = output.tolist()

        return jsonify({
            "success": True,
            "prediction": prediction
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "success": False,
            "error": str(e)
        })
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
        # Create Project Document
        # user/deviceId/dataset_session/project
        # -----------------------------
        project_ref = (
            worker_db
            .collection("user")
            .document(device_id)
            .collection("dataset_session")
            .document(project_name)
        )

        project_ref.set({

            "project":
                project_name,

            "created_at":
                firestore.SERVER_TIMESTAMP

        }, merge=True)

        # -----------------------------
        # Create Class Document
        # user/deviceId/dataset_session/project/class/className
        # -----------------------------
        class_ref = (
            worker_db
            .collection("user")
            .document(device_id)
            .collection("dataset_session")
            .document(project_name)
            .collection("class")
            .document(class_name)
        )

        class_ref.set({

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
                class_name,

            "total_images":
                total_images

        })

    except Exception as ex:

        traceback.print_exc()

        return jsonify({

            "success": False,

            "message":
                str(ex)

        }), 500
#===============================================
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

            project_name = project_doc.id
            project_data = project_doc.to_dict() or {}

            project_item = {

                "project": project_name,
                "resize_width": 0,
                "resize_height": 0,
                "created_at": str(
                    project_data.get("created_at", "")
                ),
                "classes": []

            }

            classes = (
                worker_db
                .collection("user")
                .document(device_id)
                .collection("dataset_session")
                .document(project_name)
                .collection("class")
                .stream()
            )

            total_project_images = 0
            first_class = True

            for class_doc in classes:

                class_data = class_doc.to_dict() or {}

                # ใช้ Class ตัวแรกเป็นค่า Pixel ของ Project
                if first_class:

                    project_item["resize_width"] = class_data.get(
                        "resize_width", 0
                    )

                    project_item["resize_height"] = class_data.get(
                        "resize_height", 0
                    )

                    first_class = False

                total_images = class_data.get(
                    "total_images", 0
                )

                total_project_images += total_images

                project_item["classes"].append({

                    "project": project_name,

                    "label": class_data.get(
                        "label",
                        class_doc.id
                    ),

                    "resize_width": class_data.get(
                        "resize_width", 0
                    ),

                    "resize_height": class_data.get(
                        "resize_height", 0
                    ),

                    "total_images": total_images,

                    "updated_at": str(
                        class_data.get(
                            "updated_at",
                            ""
                        )
                    )

                })

            project_item["total_classes"] = len(
                project_item["classes"]
            )

            project_item["total_images"] = (
                total_project_images
            )

            result.append(project_item)

        return jsonify({

            "success": True,
            "count": len(result),
            "data": result

        })

    except Exception as ex:

        return jsonify({

            "success": False,
            "message": str(ex)

        }), 500
 
#======================================================
@app.route("/upload_dataset_image", methods=["POST"])
def upload_dataset_image():

    try:

        data = request.get_json()

        device_id = data["deviceId"]
        project = data["project"]
        label = data["label"]
        image_base64 = data["image"]

        # --------------------------
        # class document
        # --------------------------
        class_ref = (
            worker_db
            .collection("user")
            .document(device_id)
            .collection("dataset_session")
            .document(project)
            .collection("class")
            .document(label)
        )

        class_doc = class_ref.get()

        if not class_doc.exists:

            return jsonify({
                "status": "error",
                "message": "class not found"
            }), 404

        class_data = class_doc.to_dict()

        resize_width = class_data["resize_width"]
        resize_height = class_data["resize_height"]

        # --------------------------
        # decode image
        # --------------------------
        image_bytes = base64.b64decode(
            image_base64
        )

        image = Image.open(
            BytesIO(image_bytes)
        )

        # --------------------------
        # resize
        # --------------------------
        image = image.resize(
            (resize_width, resize_height)
        )

        buffer = BytesIO()

        image.save(
            buffer,
            format="JPEG",
            quality=90
        )

        resized_bytes = buffer.getvalue()

        # --------------------------
        # filename
        # --------------------------
        image_id = str(uuid.uuid4())

        storage_path = (
            f"{device_id}/"
            f"{project}/"
            f"{label}/"
            f"{image_id}.jpg"
        )

        # --------------------------
        # upload storage
        # --------------------------
        blob = bucket.blob(storage_path)

        blob.upload_from_string(
            resized_bytes,
            content_type="image/jpeg"
        )

        # --------------------------
        # image metadata
        # --------------------------
        image_ref = (
            class_ref
            .collection("images")
            .document(image_id)
        )

        image_ref.set({

            "image_id": image_id,

            "storage_path": storage_path,
            "status": "active",

             "source": "mobile",
             "width": resize_width,
            "height": resize_height,

            "created_at":
                firestore.SERVER_TIMESTAMP
        })

        # --------------------------
        # update counter
        # --------------------------
        class_ref.update({

            "total_images":
                firestore.Increment(1),

            "updated_at":
                firestore.SERVER_TIMESTAMP
        })

        return jsonify({

            "status": "success",

            "image_id": image_id,
            "status": "success",

            "storage_path": storage_path
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500   
#======================================================
@app.route("/train_dataset", methods=["POST"])
def train_dataset():

    try:

        data = request.get_json()

        device_id = data["deviceId"]
        project = data["project"]

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
            "created_at": firestore.SERVER_TIMESTAMP
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

                image_data = image_doc.to_dict()

                storage_path = image_data["storage_path"]

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
                firestore.SERVER_TIMESTAMP
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

        data = request.get_json()

        device_id = data["deviceId"]
        job_id = data["jobId"]

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
#=====================================================
 
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