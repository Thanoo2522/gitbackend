import os
import json
import tempfile
import traceback

import firebase_admin
from firebase_admin import credentials, storage
from flask import Flask, request, jsonify
import tensorflow as tf
import tensorflowjs as tfjs

app = Flask(__name__)

# ==========================================================
# Firebase init: ใช้วิธีเดียวกับ backend หลัก (WORKER_FIREBASE_KEY
# เป็น JSON ของ service account เก็บไว้ใน env var) ไม่ใช่
# credentials.ApplicationDefault() เพราะ backend หลักไม่ได้ใช้แบบนั้น
# ==========================================================
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")

if not WORKER_FIREBASE_KEY:
    raise RuntimeError("Missing WORKER_FIREBASE_KEY")

worker_cred = credentials.Certificate(json.loads(WORKER_FIREBASE_KEY))

worker_app = firebase_admin.initialize_app(
    worker_cred,
    {
        "storageBucket": "basework-51f3b.firebasestorage.app"
    }
)

bucket = storage.bucket(app=worker_app)


@app.route("/convert", methods=["POST"])
def convert():
    """
    รับ email + project มา แล้ว:
    1. โหลด SavedModel จาก  {email}/{project}/model/saved_model/
    2. convert เป็น tfjs
    3. อัปโหลดผลลัพธ์กลับไปที่ {email}/{project}/model/tfjs/
    """
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")

    if not email or not project:
        return jsonify({
            "success": False,
            "message": "Missing email or project"
        }), 400

    saved_model_prefix = f"{email}/{project}/model/saved_model/"
    tfjs_prefix = f"{email}/{project}/model/tfjs"

    blobs = list(bucket.list_blobs(prefix=saved_model_prefix))
    if not blobs:
        return jsonify({
            "success": False,
            "message": "ไม่พบ SavedModel กรุณา train ใหม่"
        }), 404

    try:
        with tempfile.TemporaryDirectory() as tmp_in, \
             tempfile.TemporaryDirectory() as tmp_out:

            # ---------- โหลด SavedModel ทั้งโฟลเดอร์ลงมา ----------
            for blob in blobs:
                relative = blob.name[len(saved_model_prefix):]
                if not relative:
                    continue

                local_path = os.path.join(tmp_in, relative)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                blob.download_to_filename(local_path)

            # ---------- โหลดโมเดลแล้ว convert เป็น tfjs ----------
            model = tf.keras.models.load_model(tmp_in)
            tfjs.converters.save_keras_model(model, tmp_out)

            # ---------- อัปโหลดไฟล์ tfjs กลับขึ้น bucket ----------
            for fname in os.listdir(tmp_out):
                fpath = os.path.join(tmp_out, fname)
                blob = bucket.blob(f"{tfjs_prefix}/{fname}")
                blob.upload_from_filename(fpath)

        return jsonify({
            "success": True,
            "message": "Converted to tfjs successfully"
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})
