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
from PIL import (
    Image,
    ImageEnhance,
    ImageChops
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
# ============================================ 
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
            # -------------------------
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
# CHECK REGISTER
# =========================================================
@app.route("/check-register", methods=["POST"])
def check_register():

    try:

        body = request.get_json(
            silent=True
        ) or {}

        email = body.get("email")

        if not email:
            return jsonify({
                "registered": False
            })

        doc = worker_db.collection("user") \
            .document(email) \
            .get()

        if not doc.exists:
            return jsonify({
                "registered": False
            })

        data = doc.to_dict()

        return jsonify({
            "registered":
                data.get(
                    "register",
                    False
                )
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

        body = request.get_json() or {}

        email = (
            body.get("email", "")
            .lower()
            .strip()
        )

        password = body.get(
            "password", ""
        )

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

        user = doc.to_dict()

        if (
            user["password"]
            != password
        ):

            return jsonify({
                "status": "error",
                "message": "invalid password"
            }), 401

        return jsonify({

            "status": "success",

            "email":
                user["email"],

            "fullname":
                user["fullname"],

            "worker_id":
                user["worker_id"]

        })

    except Exception as e:

        return jsonify({

            "status": "error",

            "message": str(e)

        }), 500
 # =========================================================
# CREATE PROJECT  
# =========================================================
@app.route("/create_project", methods=["POST"])
def create_project():

    try:

        data = request.get_json()

        email = (   data.get("email", "")  .lower()   .strip())
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
        if not email:

                return jsonify({
               "success": False,
              "message": "email missing"
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
              f"{email}/"
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
            .document(email)
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
            .document(email)
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
            "email":
                     email,

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
# delete class
@app.route("/delete_class", methods=["POST"])
def delete_class():
    try:
        data = request.json

        email = data["email"]
        project = data["project"]
        className = data["className"]

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
        return jsonify({
            "status": "error",
            "message": str(e)
        })        
#===============================================
@app.route("/get_projects", methods=["POST"])
def get_projects():

    try:

        data = request.get_json() or {}

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

        result = []

        projects = (
            worker_db
            .collection("user")
            .document(email)
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
                    project_data.get(
                        "created_at", ""
                    )
                ),
                "classes": []

            }

            classes = (
                worker_db
                .collection("user")
                .document(email)
                .collection("dataset_session")
                .document(project_name)
                .collection("class")
                .stream()
            )

            total_project_images = 0
            first_class = True

            for class_doc in classes:

                class_data = (
                    class_doc.to_dict()
                    or {}
                )

                if first_class:

                    project_item[
                        "resize_width"
                    ] = class_data.get(
                        "resize_width", 0
                    )

                    project_item[
                        "resize_height"
                    ] = class_data.get(
                        "resize_height", 0
                    )

                    first_class = False

                total_images = (
                    class_data.get(
                        "total_images", 0
                    )
                )

                total_project_images += (
                    total_images
                )

                project_item[
                    "classes"
                ].append({

                    "project":
                        project_name,

                    "label":
                        class_data.get(
                            "label",
                            class_doc.id
                        ),

                    "resize_width":
                        class_data.get(
                            "resize_width",
                            0
                        ),

                    "resize_height":
                        class_data.get(
                            "resize_height",
                            0
                        ),

                    "total_images":
                        total_images,

                    "updated_at":
                        str(
                            class_data.get(
                                "updated_at",
                                ""
                            )
                        )

                })

            project_item[
                "total_classes"
            ] = len(
                project_item["classes"]
            )

            project_item[
                "total_images"
            ] = total_project_images

            result.append(
                project_item
            )

        return jsonify({

            "success": True,

            "count": len(result),

            "data": result

        })

    except Exception as ex:

        traceback.print_exc()

        return jsonify({

            "success": False,

            "message": str(ex)

        }), 500
 
#======================================================
    
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
            firestore.SERVER_TIMESTAMP

    })  
         #===================================================  
def update_firestore(

    email,

    project,

    class_name,

    increase=1

):

    # =====================================
    # Class Document
    # =====================================

    doc_ref = (

        worker_db
        .collection("user")
        .document(email)
        .collection("dataset_session")
        .document(project)
        .collection("class")
        .document(class_name)

    )

    # =====================================
    # Read Current Total
    # =====================================

    doc = doc_ref.get()

    if doc.exists:

        current = doc.to_dict()

        total_images = current.get(

            "total_images",

            0

        )

    else:

        total_images = 0

    total_images += increase

    # =====================================
    # Update Class
    # =====================================

    doc_ref.set(

        {

            "project": project,

            "label": class_name,

            "total_images": total_images,

            "updated_at":
                firestore.SERVER_TIMESTAMP

        },

        merge=True

    )

    return total_images
        
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
def upload_single(data):

    # ==========================
    # Read Request
    # ==========================

    email = data["email"]

    project = data["project"]

    class_name = data["className"]

    resize_width = int(
        data["resizeWidth"]
    )

    resize_height = int(
        data["resizeHeight"]
    )

    camera_source = data.get(
        "cameraSource",
        "browser"
    )

    image_base64 = data["image"]

    # ==========================
    # Decode
    # ==========================

    image = decode_base64(
        image_base64
    )

    # ==========================
    # Resize
    # ==========================

    image = resize_image(

        image,

        resize_width,

        resize_height

    )

    # ==========================
    # Calculate File Size
    # ==========================

    buffer = io.BytesIO()

    image.save(

        buffer,

        format="JPEG",

        quality=95

    )

    file_size = buffer.tell()

    # ==========================
    # Upload Firebase Storage
    # ==========================

    upload_result = upload_image(

        image=image,

        email=email,

        project=project,

        class_name=class_name,

        camera_source=camera_source,

        image_type="original"

    )

    # ==========================
    # Save Image Document
    # ==========================

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

    # ==========================
    # Update Firestore Summary
    # ==========================

    total_images = update_firestore(

        email=email,

        project=project,

        class_name=class_name,

        increase=1

    )

    # ==========================
    # Response
    # ==========================

    return {

        "success": True,

        "captureMode": "single",

        "filename":
            upload_result["filename"],

        "storagePath":
            upload_result["storagePath"],

        "imageUrl":
            upload_result["imageUrl"],

        "cameraSource":
            camera_source,

        "totalImages":
            total_images,

        "width":
            resize_width,

        "height":
            resize_height,

        "fileSize":
            file_size,

        "fileSizeKB":
            round(file_size / 1024, 1)

    }
 #===================================================  
def upload_burst(data):

    # ==========================
    # Read Request
    # ==========================

    email = data["email"]

    project = data["project"]

    class_name = data["className"]

    resize_width = int(
        data["resizeWidth"]
    )

    resize_height = int(
        data["resizeHeight"]
    )

    camera_source = data.get(
        "cameraSource",
        "browser"
    )

    images = data["images"]

    uploaded_files = []

    total_size = 0

    # ==========================
    # Upload Images
    # ==========================

    for index, image_base64 in enumerate(images):

        # ----------------------
        # Decode
        # ----------------------

        image = decode_base64(
            image_base64
        )

        # ----------------------
        # Resize
        # ----------------------

        image = resize_image(

            image,

            resize_width,

            resize_height

        )

        # ----------------------
        # Calculate File Size
        # ----------------------

        buffer = io.BytesIO()

        image.save(

            buffer,

            format="JPEG",

            quality=95

        )

        file_size = buffer.tell()

        total_size += file_size

        # ----------------------
        # Upload Firebase Storage
        # ----------------------

        upload_result = upload_image(

            image=image,

            email=email,

            project=project,

            class_name=class_name,

            camera_source=camera_source,

            image_type=f"burst_{index+1}"

        )

        # ----------------------
        # Save Firestore
        # ----------------------

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

        # ----------------------
        # Response List
        # ----------------------

        uploaded_files.append({

            "type":
                f"burst_{index+1}",

            "filename":
                upload_result["filename"],

            "storagePath":
                upload_result["storagePath"],

            "imageUrl":
                upload_result["imageUrl"],

            "fileSize":
                file_size,

            "fileSizeKB":
                round(file_size / 1024, 1)

        })

    # ==========================
    # Update Firestore Summary
    # ==========================

    total_images = update_firestore(

        email=email,

        project=project,

        class_name=class_name,

        increase=len(images)

    )

    # ==========================
    # Calculate Average Size
    # ==========================

    average_size = (

        round(total_size / len(images))

        if images else 0

    )

    # ==========================
    # Response
    # ==========================

    return {

        "success": True,

        "captureMode":
            "burst",

        "uploaded":
            len(images),

        "cameraSource":
            camera_source,

        "width":
            resize_width,

        "height":
            resize_height,

        "totalImages":
            total_images,

        "totalSize":
            total_size,

        "totalSizeKB":
            round(total_size / 1024, 1),

        "averageSize":
            average_size,

        "averageSizeKB":
            round(average_size / 1024, 1),

        "files":
            uploaded_files

    }  
 #====================================================
def upload_generator(data):

    # ==========================
    # Read Request
    # ==========================

    email = data["email"]

    project = data["project"]

    class_name = data["className"]

    resize_width = int(
        data["resizeWidth"]
    )

    resize_height = int(
        data["resizeHeight"]
    )

    camera_source = data.get(
        "cameraSource",
        "browser"
    )

    image_base64 = data["image"]

    # ==========================
    # Decode
    # ==========================

    image = decode_base64(
        image_base64
    )

    # ==========================
    # Resize
    # ==========================

    image = resize_image(

        image,

        resize_width,

        resize_height

    )

    # ==========================
    # Generate Images
    # ==========================

    generated_images = generate_images(
        image
    )

    uploaded_files = []

    total_size = 0

    # ==========================
    # Upload Images
    # ==========================

    for image_type, img in generated_images:

        # ----------------------
        # Calculate File Size
        # ----------------------

        buffer = io.BytesIO()

        img.save(

            buffer,

            format="JPEG",

            quality=95

        )

        file_size = buffer.tell()

        total_size += file_size

        # ----------------------
        # Upload Firebase Storage
        # ----------------------

        upload_result = upload_image(

            image=img,

            email=email,

            project=project,

            class_name=class_name,

            camera_source=camera_source,

            image_type=image_type

        )

        # ----------------------
        # Save Firestore
        # ----------------------

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

            "type":
                image_type,

            "filename":
                upload_result["filename"],

            "storagePath":
                upload_result["storagePath"],

            "imageUrl":
                upload_result["imageUrl"],

            "fileSize":
                file_size,

            "fileSizeKB":
                round(file_size / 1024, 1)

        })

    # ==========================
    # Update Firestore Summary
    # ==========================

    total_images = update_firestore(

        email=email,

        project=project,

        class_name=class_name,

        increase=len(generated_images)

    )

    # ==========================
    # Calculate Average Size
    # ==========================

    average_size = (

        round(total_size / len(generated_images))

        if generated_images else 0

    )

    # ==========================
    # Response
    # ==========================

    return {

        "success": True,

        "captureMode":
            "generator",

        "generated":
            len(generated_images),

        "cameraSource":
            camera_source,

        "width":
            resize_width,

        "height":
            resize_height,

        "totalImages":
            total_images,

        "totalSize":
            total_size,

        "totalSizeKB":
            round(total_size / 1024, 1),

        "averageSize":
            average_size,

        "averageSizeKB":
            round(average_size / 1024, 1),

        "files":
            uploaded_files

    }
#=====================================================
@app.route(
    "/upload_dataset_image",
    methods=["POST"]
)
def upload_dataset_image():

    try:

        data = request.get_json()

        if not data:

            return jsonify({

                "success": False,

                "message": "No JSON data"

            }), 400

        capture_mode = data.get(

            "captureMode",

            "single"

        ).lower()


        # ==========================
        # Single Capture
        # ==========================

        if capture_mode == "single":

            result = upload_single(data)


        # ==========================
        # Burst Capture
        # ==========================

        elif capture_mode == "burst":

            result = upload_burst(data)


        # ==========================
        # AI Dataset Generator
        # ==========================

        elif capture_mode == "generator":

            result = upload_generator(data)


        # ==========================
        # Unknown Mode
        # ==========================

        else:

            return jsonify({

                "success": False,

                "message":

                f"Unknown captureMode : {capture_mode}"

            }), 400


        return jsonify(result)


    except Exception as ex:

        print(ex)

        return jsonify({

            "success": False,

            "message": str(ex)

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