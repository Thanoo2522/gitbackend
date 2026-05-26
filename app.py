from flask import Flask, request, jsonify, render_template
import os, json, traceback, requests, time, threading
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore, storage
from PIL import Image
from io import BytesIO
import uuid
import numpy as np
import tensorflow as tf
import zipfile

app = Flask(__name__)

# =========================================================
# ENV
# =========================================================
HUB_FIREBASE_KEY = os.environ.get("HUB_FIREBASE_KEY")
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
SERVER_ID = os.environ.get("SERVER_ID")
WORKER_WEBHOOK_URL = os.environ.get("WORKER_WEBHOOK_URL")

# =========================================================
# FIREBASE INIT
# =========================================================
hub_cred = credentials.Certificate(json.loads(HUB_FIREBASE_KEY))
hub_app = firebase_admin.initialize_app(hub_cred, name="hub")
hub_db = firestore.client(hub_app)

worker_cred = credentials.Certificate(json.loads(WORKER_FIREBASE_KEY))
worker_app = firebase_admin.initialize_app(
    worker_cred,
    {"storageBucket": "basework-51f3b.firebasestorage.app"},
    name="worker"
)

worker_db = firestore.client(worker_app)
bucket = storage.bucket(app=worker_app)

# =========================================================
# MODELS
# =========================================================
models = {}

labels = {
    "imagenumber": ["1", "2"]
}

# =========================================================
# HEARTBEAT (UNCHANGED LOGIC)
# =========================================================
def heartbeat():

    while True:
        try:
            hub_db.collection("hub_system") \
                .document("server_pool") \
                .collection("servers") \
                .document(SERVER_ID) \
                .set({
                    "status": "online",
                    "cloud_url": WORKER_WEBHOOK_URL,
                    "last_heartbeat": int(time.time())
                }, merge=True)

        except Exception as e:
            print("heartbeat error", e)

        time.sleep(30)

threading.Thread(target=heartbeat, daemon=True).start()

# =========================================================
# HOME
# =========================================================
@app.route("/")
def home():
    return f"{SERVER_ID} RUNNING"


# =========================================================
# STRICT VDO (FIX REGISTER LOOP)
# =========================================================
@app.route("/vdo")
def vdo_page():

    project = request.args.get("project", "imagenumber")
    user_id = request.args.get("user_id")

    if not user_id:
        return "missing user_id", 403

    doc = worker_db.collection("user").document(user_id).get()

    if not doc.exists:
        return "NOT REGISTERED", 403

    return render_template("vdo.html", project=project, user_id=user_id)


# =========================================================
# CHECK REGISTER (WORKER)
# =========================================================
@app.route("/check-register", methods=["POST"])
def check_register():

    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")

    if not user_id:
        return jsonify({"registered": False})

    doc = worker_db.collection("user").document(user_id).get()

    return jsonify({
        "registered": doc.exists and doc.to_dict().get("register", False)
    })


# =========================================================
# REGISTER USER (NO DUPLICATE)
# =========================================================
@app.route("/register-user", methods=["POST"])
def register_user():

    try:
        body = request.get_json(silent=True) or {}
        user_id = body.get("user_id")

        if not user_id:
            return jsonify({"status": "error"}), 400

        ref = worker_db.collection("user").document(user_id)
        doc = ref.get()

        if doc.exists:
            return jsonify({"status": "success", "message": "already registered"})

        ref.set({
            "userId": user_id,
            "fullname": body.get("name", ""),
            "phone": body.get("phone", ""),
            "email": body.get("email", ""),
            "register": True,
            "worker_id": SERVER_ID,
            "created_at": datetime.utcnow()
        })

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================================================
# MAIN ROUTE (UNCHANGED LOGIC)
# =========================================================
@app.route("/main-route", methods=["POST"])
def main_route():

    body = request.get_json(silent=True) or {}

    events = body.get("events", [])

    for event in events:

        if event.get("type") != "message":
            continue

        text = event.get("message", {}).get("text", "")

        if text.lower() == "ping":
            return jsonify({"reply": "pong"})

    return jsonify({"status": "ok"})


# =========================================================
# SAFE VDO LINK GENERATOR (NO LOOP)
# =========================================================
def open_vdo(event, parts):

    reply_token = event.get("replyToken")

    if len(parts) < 2:
        return jsonify({"error": "bad format"})

    project = parts[1]

    url = f"{WORKER_WEBHOOK_URL}/vdo?project={project}"

    return jsonify({
        "reply": f"OPEN VDO:\n{url}"
    })


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))