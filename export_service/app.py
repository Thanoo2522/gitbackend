"""
==============================================================
🌐 EXPORT SERVICE (CPU only — ไม่ต้องใช้ GPU)
==============================================================
แยกออกมาจาก gitbackend-gpu โดยเฉพาะ เพราะงาน export (.pt -> .onnx/.tflite/.ncnn)
เป็นแค่การแปลงไฟล์ ไม่ได้เทรนโมเดล ไม่จำเป็นต้องใช้ GPU เลย
ถ้ารันอยู่ใน service เดียวกับตัวเทรน (ที่มี GPU + Instance-based billing)
จะถูกคิดค่า GPU ตลอดเวลาที่ instance ทำงาน แม้ตอนนั้นจะแค่ export ไฟล์เฉยๆ ก็ตาม

Deploy service นี้แยกเป็น Cloud Run service ใหม่:
  - ไม่ต้องติ๊ก GPU
  - Billing: Request-based ก็พอ (ไม่มี background thread ยาวๆ แบบตัวเทรน)
  - Memory: 2-4 GiB ก็เพียงพอสำหรับโหลด YOLOv8 nano มา export

ต้องตั้งค่า Environment Variable ตัวเดียว:
  WORKER_FIREBASE_KEY  (Service Account JSON เดียวกับที่ gitbackend-gpu ใช้
                        เพื่อให้เข้าถึง Storage bucket เดียวกัน อ่าน best.pt
                        ที่เทรนไว้ และเขียนไฟล์ export กลับไปที่เดิม)
==============================================================
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

import os
import json
import io
import threading
import tempfile
import shutil
import traceback
import zipfile
import requests

import firebase_admin
from firebase_admin import credentials, firestore, storage

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
# ENV
# =========================================================
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")

if not WORKER_FIREBASE_KEY:
    raise RuntimeError("Missing WORKER_FIREBASE_KEY")

# =========================================================
# FIREBASE (ต่อ storage bucket เดียวกับ gitbackend-gpu)
# =========================================================
worker_cred = credentials.Certificate(json.loads(WORKER_FIREBASE_KEY))

worker_app = firebase_admin.initialize_app(
    worker_cred,
    {
        "storageBucket": "basework-51f3b.firebasestorage.app"
    },
    name="worker_export"
)

worker_db = firestore.client(worker_app)
bucket = storage.bucket(app=worker_app)


@app.route("/")
def home():
    return "EXPORT SERVICE RUNNING (CPU only)"


# =========================================================
# 🌐 EXPORT FOR EDGE (ONNX / TFLite / NCNN / TensorRT)
# ย้ายมาจาก gitbackend-gpu ทั้งชุด ตรรกะเดิมทุกอย่างไม่เปลี่ยน
# ต่างกันแค่ service นี้ไม่มี GPU ให้ใช้ (ไม่จำเป็นสำหรับงาน export)
# =========================================================

edge_export_status = {}   # key = f"{email}/{project}/{edge_format}"

VALID_EDGE_FORMATS = ("onnx", "tflite", "ncnn", "engine")

# 🌐 TFLITE_SERVICE_URL (ไม่บังคับ แต่ต้องตั้งค่าถ้าจะใช้ TFLite export)
# ⚠️ เหตุผลที่ต้องแยก TFLite ออกเป็น service อื่น:
# torch/ultralytics (ที่ใช้ export ONNX/NCNN) กับ tensorflow (ที่ใช้แปลง TFLite)
# ชนกันที่ตัว numpy เสมอไม่ว่าจะ pin เวอร์ชันไหนก็ตาม (เจอ AttributeError หลาย
# แบบสลับกันไปทุกครั้งที่ปรับเวอร์ชัน) เพราะทั้งสองฝั่งต้องการ numpy ABI ต่างกัน
# วิธีแก้ที่ยั่งยืน: ให้ export_service (torch, ไม่มี tensorflow) export เป็น
# ONNX ก่อนเสมอ แล้วส่งไฟล์ .onnx ไปให้ tflite_service (tensorflow ล้วนๆ ไม่มี
# torch) แปลงเป็น .tflite ให้ต่างหาก คนละ container คนละ environment กันเลย
TFLITE_SERVICE_URL = os.environ.get("TFLITE_SERVICE_URL")

EDGE_FORMAT_MAP = {
    "onnx": "onnx",
    "ncnn": "ncnn",
}

EDGE_FORMAT_IS_DIR = {
    "onnx": False,
    "tflite": False,   # ตอนนี้ได้ไฟล์ .tflite เดี่ยวกลับมาจาก tflite_service ไม่ใช่ directory แล้ว
    "ncnn": True,
}


def _build_trt_zip(zf, exported_onnx_path, project):
    """
    สร้างเนื้อหา zip สำหรับกรณี edge_format == "engine":
    แนบ model.onnx + สคริปต์ build_engine.sh + README.txt
    (ไม่ build .engine จริงที่นี่ เพราะ service นี้ไม่มี GPU เลย)
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
echo "นำไปใช้กับ project: {project}"
"""
    zf.writestr("build_engine.sh", build_script)

    readme = """# วิธีใช้งาน

1. คัดลอกไฟล์ model.onnx และ build_engine.sh ไปไว้บนบอร์ด Jetson ที่จะ deploy จริง
2. รันคำสั่ง: chmod +x build_engine.sh && ./build_engine.sh
3. จะได้ไฟล์ model.engine สำหรับใช้งานบนบอร์ดนั้นโดยเฉพาะ
   (ห้ามนำ .engine ไปใช้ข้ามบอร์ดรุ่นอื่น ต้อง build ใหม่ทุกครั้งที่เปลี่ยนบอร์ด)
"""
    zf.writestr("README.txt", readme)


def run_edge_export(email, project, edge_format):
    key = f"{email}/{project}/{edge_format}"
    tmp_dir = tempfile.mkdtemp(prefix="edge_export_")

    try:
        from ultralytics import YOLO

        edge_export_status[key]["progress"] = 10

        # ----------------------------------------------------
        # 1) ดาวน์โหลด best.pt จาก Storage มาไว้ที่ tmp_dir ก่อน
        # ----------------------------------------------------
        model_storage_path = f"{email}/{project}/model_det_seg/best.pt"
        model_blob = bucket.blob(model_storage_path)

        if not model_blob.exists():
            raise ValueError("ไม่พบ best.pt กรุณา train โมเดลให้เสร็จก่อน")

        local_pt_path = os.path.join(tmp_dir, "best.pt")
        model_blob.download_to_filename(local_pt_path)

        edge_export_status[key]["progress"] = 30

        # ----------------------------------------------------
        # 2) โหลดโมเดลแล้วสั่ง export (รันบน CPU ล้วนๆ ไม่มี GPU)
        # ----------------------------------------------------
        model = YOLO(local_pt_path)

        if edge_format == "engine":
            export_kwargs = {"format": "onnx", "imgsz": 640}
            exported_path = model.export(**export_kwargs)

        elif edge_format == "tflite":
            # ----------------------------------------------------
            # 2a) TFLite: export เป็น ONNX ก่อนด้วย ultralytics (torch ล้วนๆ
            #     ไม่แตะ tensorflow เลยตรงนี้) แล้วส่งไฟล์ไปให้ tflite_service
            #     (คนละ container, มีแค่ tensorflow ไม่มี torch) แปลงให้แทน
            # ----------------------------------------------------
            if not TFLITE_SERVICE_URL:
                raise RuntimeError(
                    "ยังไม่ได้ตั้งค่า TFLITE_SERVICE_URL — ต้อง deploy tflite_service "
                    "แยกต่างหากก่อน แล้วตั้งค่า Environment Variable นี้ใน export_service"
                )

            onnx_path = model.export(format="onnx", imgsz=640)
            edge_export_status[key]["progress"] = 55

            with open(onnx_path, "rb") as f:
                tflite_resp = requests.post(
                    f"{TFLITE_SERVICE_URL.rstrip('/')}/convert_to_tflite",
                    files={"model": ("model.onnx", f, "application/octet-stream")},
                    timeout=280
                )

            tflite_resp.raise_for_status()
            tflite_result = tflite_resp.json()

            if not tflite_result.get("success"):
                raise RuntimeError(f"tflite_service แปลงไฟล์ไม่สำเร็จ: {tflite_result.get('message')}")

            import base64
            tflite_bytes = base64.b64decode(tflite_result["tflite_base64"])
            exported_path = os.path.join(tmp_dir, tflite_result.get("filename", "model_float32.tflite"))
            with open(exported_path, "wb") as out_f:
                out_f.write(tflite_bytes)

        else:
            export_kwargs = {"format": EDGE_FORMAT_MAP[edge_format], "imgsz": 640}
            exported_path = model.export(**export_kwargs)

        edge_export_status[key]["progress"] = 80

        # ----------------------------------------------------
        # 3) Zip ผลลัพธ์ทั้งหมดแล้วอัปโหลดขึ้น Storage (ที่เดียวกับ gitbackend-gpu)
        # ----------------------------------------------------
        zip_local_path = os.path.join(tmp_dir, f"edge_{edge_format}.zip")

        with zipfile.ZipFile(zip_local_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if edge_format == "engine":
                _build_trt_zip(zf, exported_path, project)
            elif EDGE_FORMAT_IS_DIR.get(edge_format) and os.path.isdir(exported_path):
                for root, _, files in os.walk(exported_path):
                    for fname in files:
                        local_file = os.path.join(root, fname)
                        arcname = os.path.relpath(local_file, tmp_dir)
                        zf.write(local_file, arcname)
            else:
                zf.write(exported_path, os.path.basename(exported_path))

            labels_path = f"{email}/{project}/model_det_seg/classes.json"
            labels_blob = bucket.blob(labels_path)
            if labels_blob.exists():
                zf.writestr("classes.json", labels_blob.download_as_bytes())

        edge_storage_path = f"{email}/{project}/model_edge/{edge_format}.zip"
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
    edge_format = (data.get("edgeFormat") or "onnx").strip().lower()

    if not email or not project:
        return jsonify({"success": False, "message": "Missing email or project"}), 400

    if edge_format not in VALID_EDGE_FORMATS:
        return jsonify({
            "success": False,
            "message": f"edgeFormat must be one of {VALID_EDGE_FORMATS}"
        }), 400

    model_storage_path = f"{email}/{project}/model_det_seg/best.pt"
    if not bucket.blob(model_storage_path).exists():
        return jsonify({
            "success": False,
            "message": "ไม่พบโมเดลที่ train แล้ว กรุณา train ให้เสร็จก่อน"
        }), 404

    key = f"{email}/{project}/{edge_format}"
    edge_export_status[key] = {"status": "running", "progress": 0}

    thread = threading.Thread(
        target=run_edge_export,
        args=(email, project, edge_format)
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
    edge_format = (data.get("edgeFormat") or "onnx").strip().lower()

    key = f"{email}/{project}/{edge_format}"
    resp = jsonify(edge_export_status.get(key, {"status": "idle"}))
    resp.headers.add("Access-Control-Allow-Origin", "*")
    return resp


@app.route("/download_edge_model", methods=["POST"])
def download_edge_model():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    project = data.get("project")
    edge_format = (data.get("edgeFormat") or "onnx").strip().lower()

    if not email or not project or edge_format not in VALID_EDGE_FORMATS:
        return jsonify({"success": False, "message": "Invalid request"}), 400

    edge_storage_path = f"{email}/{project}/model_edge/{edge_format}.zip"
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
        download_name=f"{project}_{edge_format}.zip"
    )


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
