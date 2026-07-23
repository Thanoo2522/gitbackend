"""
==============================================================
🔄 TFLITE CONVERTER SERVICE (tensorflow ล้วนๆ — ไม่มี torch เลย)
==============================================================
แยกออกมาจาก export_service โดยเฉพาะ เพราะ torch/ultralytics (ที่ใช้ export
ONNX/NCNN) กับ tensorflow (ที่ใช้แปลง TFLite ผ่าน onnx2tf) ชนกันที่ numpy ABI
เสมอไม่ว่าจะลอง pin เวอร์ชันคู่ไหนก็ตาม (เจอ AttributeError หลายแบบสลับกันไป
ทุกครั้งที่ปรับเวอร์ชัน) — วิธีแก้ที่ยั่งยืนคือแยกเป็นคนละ container ไปเลย

Service นี้ทำหน้าที่เดียว: รับไฟล์ .onnx เข้ามา (ที่ export_service สร้างไว้แล้ว
ด้วย torch/ultralytics) แปลงเป็น .tflite ด้วย onnx2tf แล้วส่งไฟล์กลับไป
เป็น stateless เต็มรูปแบบ ไม่ต้องต่อ Firebase เลย เพราะรับ-ส่งไฟล์ตรงๆ ผ่าน
HTTP request/response เท่านั้น

Deploy เป็น Cloud Run service แยกต่างหาก:
  - ไม่ต้องติ๊ก GPU
  - Billing: Request-based พอ
  - Memory: 2-4 GiB เพียงพอ
  - ไม่ต้องตั้งค่า Environment Variable ใดๆ เลย (ไม่แตะ Firebase)

หลัง deploy แล้ว เอา URL ของ service นี้ไปตั้งเป็น Environment Variable
TFLITE_SERVICE_URL ใน service "export_service" (ตัวที่เรียกใช้ service นี้)
==============================================================
"""

from flask import Flask, request, jsonify
from flask_cors import CORS

import os
import base64
import tempfile
import shutil
import traceback

app = Flask(__name__)
CORS(
    app,
    resources={
        r"/*": {
            "origins": "*"
        }
    }
)


@app.route("/")
def home():
    return "TFLITE CONVERTER SERVICE RUNNING (tensorflow only, no torch)"


@app.route("/convert_to_tflite", methods=["POST", "OPTIONS"])
def convert_to_tflite():
    if request.method == "OPTIONS":
        response = jsonify({"success": True})
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
        response.headers.add("Access-Control-Allow-Methods", "POST,OPTIONS")
        return response, 200

    tmp_dir = tempfile.mkdtemp(prefix="tflite_convert_")

    try:
        if "model" not in request.files:
            return jsonify({"success": False, "message": "Missing 'model' file in request"}), 400

        # import ตรงนี้ (ไม่ import ไว้บนสุดของไฟล์) เพื่อไม่ให้กระทบ startup time
        # ของ service ถ้า route นี้ยังไม่เคยถูกเรียกใช้
        import onnx2tf

        onnx_file = request.files["model"]
        onnx_path = os.path.join(tmp_dir, "model.onnx")
        onnx_file.save(onnx_path)

        # ----------------------------------------------------
        # แปลง ONNX -> TensorFlow SavedModel -> TFLite ด้วย onnx2tf
        # (ได้ไฟล์ .tflite หลายแบบออกมา เช่น float32, float16, quantized)
        # ----------------------------------------------------
        output_dir = os.path.join(tmp_dir, "saved_model")
        onnx2tf.convert(
            input_onnx_file_path=onnx_path,
            output_folder_path=output_dir,
            not_use_onnxsim=True,
        )

        tflite_files = [f for f in os.listdir(output_dir) if f.endswith(".tflite")]
        if not tflite_files:
            return jsonify({
                "success": False,
                "message": "onnx2tf แปลงสำเร็จ แต่ไม่พบไฟล์ .tflite ที่สร้างออกมาเลย"
            }), 500

        # เลือกตัว float32 เป็นค่าเริ่มต้น (เข้ากันได้กว้างที่สุดกับอุปกรณ์ edge ทั่วไป)
        # ถ้าไม่มีตัวนี้ (บางโมเดล onnx2tf อาจไม่สร้างให้) ใช้ตัวแรกที่เจอแทน
        preferred = next((f for f in tflite_files if "float32" in f), tflite_files[0])
        tflite_path = os.path.join(output_dir, preferred)

        with open(tflite_path, "rb") as f:
            tflite_b64 = base64.b64encode(f.read()).decode("utf-8")

        return jsonify({
            "success": True,
            "tflite_base64": tflite_b64,
            "filename": preferred,
            "available_variants": tflite_files
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080))
    )
