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
LIFF_ID = os.environ.get("LIFF_ID")

if not WORKER_FIREBASE_KEY:
    raise RuntimeError("Missing WORKER_FIREBASE_KEY")

if not LIFF_ID:
    raise RuntimeError("Missing LIFF_ID")

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
    return jsonify({"status": "worker online"})


# =========================================================
# CONFIG (ให้ frontend ดึง API URL)
# =========================================================
@app.route("/config/<ofm>")
def config(ofm):
    return jsonify({
        "status": "ok",
        "ofm": ofm,
        "apiUrl": "https://YOUR_CLOUD_RUN_URL/register"
    })


# =========================================================
# CHECK USER
# =========================================================
@app.route("/check-user", methods=["POST"])
def check_user():
    try:
        body = request.get_json()
        user_id = body.get("userId")

        if not user_id:
            return jsonify({"registered": False})

        doc = db.collection("users").document(user_id).get()

        return jsonify({"registered": doc.exists})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"registered": False, "error": str(e)})


# =========================================================
# REGISTER USER
# =========================================================
@app.route("/register", methods=["POST"])
def register():
    try:
        body = request.get_json()

        user_id = body.get("userId")
        if not user_id:
            return jsonify({"status": "error", "message": "missing userId"})

        db.collection("users").document(user_id).set({
            "name": body.get("name"),
            "home": body.get("home"),
            "address": body.get("address"),
            "phone": body.get("phone"),
            "ofm": body.get("ofm"),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        return jsonify({"status": "ok"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


# =========================================================
# LINE OA WEBHOOK (MAIN FLOW FIXED)
# =========================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    try:
        body = request.get_json()

        print("=" * 50)
        print("LINE WEBHOOK")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print("=" * 50)

        events = body.get("events", [])
        if not events:
            return jsonify({"status": "no events"})

        event = events[0]
        source = event.get("source", {})
        user_id = source.get("userId")

        if not user_id:
            return jsonify({"status": "no userId"})

        # =================================================
        # CHECK USER IN FIRESTORE
        # =================================================
        doc = db.collection("users").document(user_id).get()

        # =================================================
        # NOT REGISTERED → SEND LIFF LINK
        # =================================================
        if not doc.exists:

            register_link = (
                f"https://liff.line.me/{LIFF_ID}"
                f"?ofm=default&userId={user_id}"
            )

            return jsonify({
                "status": "not_registered",
                "line_message": {
                    "type": "template",
                    "altText": "กรุณาลงทะเบียน",
                    "template": {
                        "type": "buttons",
                        "text": "กรุณาลงทะเบียนก่อนใช้งานระบบ",
                        "actions": [
                            {
                                "type": "uri",
                                "label": "สมัครสมาชิก",
                                "uri": register_link
                            }
                        ]
                    }
                }
            })

        # =================================================
        # REGISTERED USER
        # =================================================
        return jsonify({
            "status": "ok",
            "message": "user ready",
            "user": doc.to_dict()
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


# =========================================================
# REGISTER PAGE (LIFF ENTRY)
# =========================================================
@app.route("/register-page")
def register_page():
    return render_template("register.html")


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)