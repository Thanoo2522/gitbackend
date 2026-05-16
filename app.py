from flask import Flask, request, jsonify, render_template
import os, json, traceback
import firebase_admin
from firebase_admin import credentials, firestore

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)

# =========================================================
# ENV
# =========================================================
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")

if not WORKER_FIREBASE_KEY:
    raise RuntimeError("Missing WORKER_FIREBASE_KEY")

# =========================================================
# FIREBASE INIT
# =========================================================
worker_app = firebase_admin.initialize_app(
    credentials.Certificate(json.loads(WORKER_FIREBASE_KEY)),
    name="worker"
)

db = firestore.client(worker_app)

# =========================================================
# HEALTH CHECK
# =========================================================
@app.route("/")
def home():
    return jsonify({
        "status": "worker online"
    })

# =========================================================
# CHECK USER REGISTERED
# =========================================================
@app.route("/check-user", methods=["POST"])
def check_user():

    try:
        body = request.get_json()
        user_id = body.get("userId")

        if not user_id:
            return jsonify({"registered": False})

        doc = db.collection("users").document(user_id).get()

        return jsonify({
            "registered": doc.exists
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "registered": False,
            "error": str(e)
        })

# =========================================================
# REGISTER USER
# =========================================================
@app.route("/register", methods=["POST"])
def register():

    try:
        body = request.get_json()
        user_id = body.get("userId")

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "missing userId"
            })

        db.collection("users").document(user_id).set({
            "name": body.get("name"),
            "home": body.get("home"),
            "address": body.get("address"),
            "phone": body.get("phone"),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        return jsonify({"status": "ok"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        })

# =========================================================
# 🔥 MAIN WEBHOOK (REQUIRED ROUTE)
# =========================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    try:
        body = request.get_json()

        print("=" * 50)
        print("WORKER WEBHOOK")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print("=" * 50)

        user_id = body.get("userId")

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "no userId"
            })

        # =================================================
        # CHECK REGISTER
        # =================================================
        doc = db.collection("users").document(user_id).get()

        # =================================================
        # NOT REGISTERED → SEND LINK BACK TO LINE OA
        # =================================================
        if not doc.exists:

            # 🔥 เปิดหน้า register.html ผ่าน LIFF
            register_link = (
                f"https://liff.line.me/{LIFF_ID}"
                f"?userId={user_id}"
            )

            # =================================================
            # ส่งกลับแบบ LINE FRIENDLY (button clickable)
            # =================================================
            return jsonify({
                "status": "not_registered",
                "register_link": register_link,
                "line_message": {
                    "type": "template",
                    "altText": "กรุณาลงทะเบียน",
                    "template": {
                        "type": "buttons",
                        "text": "กรุณาลงทะเบียนก่อนใช้งาน",
                        "actions": [
                            {
                                "type": "uri",
                                "label": "👉 สมัครสมาชิก",
                                "uri": register_link
                            }
                        ]
                    }
                }
            })

        # =================================================
        # REGISTERED → NORMAL FLOW
        # =================================================
        return jsonify({
            "status": "ok",
            "message": "user ready",
            "user": doc.to_dict()
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        })

# =========================================================
# REGISTER PAGE (OPTIONAL LIFF)
# =========================================================
@app.route("/register-page")
def register_page():
    return render_template("register.html")

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)