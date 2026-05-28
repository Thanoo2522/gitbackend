# from click import command
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
import zipfile
import numpy as np
import tensorflow as tf

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)

# =========================================================
# MODELS
# =========================================================
models = {}

labels = {
    "imagenumber": ["1", "2"]
}

print("LABELS LOADED")

# =========================================================
# MODEL LOADER
# =========================================================
def get_model(project):

    if project in models:
        return models[project]

    model_path = f"models/{project}.h5"

    if not os.path.exists(model_path):
        raise Exception(f"Model not found: {model_path}")

    model = tf.keras.models.load_model(model_path)
    models[project] = model
    return model


# =========================================================
# ENV
# =========================================================
HUB_FIREBASE_KEY = os.environ.get("HUB_FIREBASE_KEY")
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
SERVER_ID = os.environ.get("SERVER_ID")
WORKER_WEBHOOK_URL = os.environ.get("WORKER_WEBHOOK_URL")
LIFF_ID = os.environ.get("LIFF_ID")

for k, v in {
    "HUB_FIREBASE_KEY": HUB_FIREBASE_KEY,
    "WORKER_FIREBASE_KEY": WORKER_FIREBASE_KEY,
    "LINE_CHANNEL_ACCESS_TOKEN": LINE_CHANNEL_ACCESS_TOKEN,
    "SERVER_ID": SERVER_ID,
    "WORKER_WEBHOOK_URL": WORKER_WEBHOOK_URL,
    "LIFF_ID": LIFF_ID
}.items():
    if not v:
        raise RuntimeError(f"Missing {k}")


# =========================================================
# FIREBASE
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
# LINE
# =========================================================
LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"

LINE_HEADERS = {
    "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

def reply_message(token, text):
    requests.post(
        LINE_REPLY_API,
        headers=LINE_HEADERS,
        json={
            "replyToken": token,
            "messages": [{"type": "text", "text": text}]
        }
    )


# =========================================================
# MAIN ROUTE (FORMAT กลาง)
# =========================================================
@app.route("/main-route", methods=["POST"])
def main_route():

    try:
        body = request.get_json(silent=True) or {}

        for event in body.get("events", []):

            if event.get("type") != "message":
                continue

            msg = event.get("message", {})
            if msg.get("type") != "text":
                continue

            text = msg.get("text", "").strip()
            parts = text.split("")

            if len(parts) == 0:
                continue

            command = parts[0].lower()

            # =====================================================
            # FORMAT กลาง (multi project support)
            # =====================================================
            if command in ["imagecolor", "imagenumber", "imagemater"]:

                if len(parts) < 2:
                    reply_message(event["replyToken"], f"{command} <label>")
                    return jsonify({"status": "error"})

                label = parts[1].lower()
                project = command

                # keep old logic
                if project == "imagenumber" and not label.isdigit():
                    reply_message(event["replyToken"], "ต้องเป็นตัวเลข")
                    return jsonify({"status": "error"})

                worker_db.collection("dataset_session") \
                    .document(event["source"]["userId"]) \
                    .set({
                        "project": project,
                        "label": label,
                        "updated_at": datetime.utcnow()
                    })

                reply_message(
                    event["replyToken"],
                    f"OK\nproject={project}\nlabel={label}"
                )

                return jsonify({"status": "success"})

            # =====================================================
            elif command == "download":
                return download_dataset(event, parts)

            else:
                reply_message(event["replyToken"], "unknown command")

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================================================
# IMAGE HANDLER (UNCHANGED LOGIC)
# =========================================================
def handle_image(event):
    try:

        user_id = event["source"]["userId"]
        reply_token = event["replyToken"]

        session = worker_db.collection("dataset_session") \
            .document(user_id).get()

        if not session.exists:
            reply_message(reply_token, "no session")
            return jsonify({"status": "error"})

        data = session.to_dict()

        project = data["project"]
        label = data["label"]

        msg_id = event["message"]["id"]

        url = f"https://api-data.line.me/v2/bot/message/{msg_id}/content"

        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
        )

        img = Image.open(BytesIO(r.content)).convert("RGB")
        img = img.resize((224, 224))

        name = str(uuid.uuid4()) + ".jpg"
        path = f"/tmp/{name}"
        img.save(path)

        storage_path = f"{project}/{label}/{name}"

        blob = bucket.blob(storage_path)
        blob.upload_from_filename(path)
        blob.make_public()

        worker_db.collection(project).document(label).collection("dataset") \
            .add({
                "image": blob.public_url,
                "created_at": datetime.utcnow()
            })

        reply_message(reply_token, "saved")

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# =========================================================
# FIXED DOWNLOAD DATASET (IMPORTANT)
# =========================================================
def download_dataset(event, parts):

    try:
        reply_token = event["replyToken"]

        if len(parts) < 2:
            reply_message(reply_token, "download <project>")
            return jsonify({"status": "error"})

        project = parts[1].lower()

        prefix = f"{project}/"

        blobs = list(bucket.list_blobs(prefix=prefix))

        blobs = [b for b in blobs if not b.name.endswith("/")]

        if not blobs:
            reply_message(reply_token, "no dataset")
            return jsonify({"status": "error"})

        zip_name = f"{project}_{uuid.uuid4().hex}.zip"
        zip_path = f"/tmp/{zip_name}"

        with zipfile.ZipFile(zip_path, "w") as z:

            for b in blobs:
                data = b.download_as_bytes()
                arc = b.name.replace(prefix, "")
                z.writestr(arc, data)

        out_blob = bucket.blob(f"downloads/{zip_name}")
        out_blob.upload_from_filename(zip_path)
        out_blob.make_public()

        reply_message(
            reply_token,
            f"READY\n{out_blob.public_url}"
        )

        return jsonify({"status": "success"})

    except Exception as e:
        traceback.print_exc()

        reply_message(
            event.get("replyToken"),
            f"ERROR {str(e)}"
        )

        return jsonify({"status": "error"}), 500


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))