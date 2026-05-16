from flask import Flask, request, jsonify, render_template
import os
import json
import traceback
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# =========================================================
# ENV
# =========================================================
WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")
SERVER_ID = os.environ.get("SERVER_ID")
WORKER_WEBHOOK_URL = os.environ.get("WORKER_WEBHOOK_URL")

worker_cred = credentials.Certificate(json.loads(WORKER_FIREBASE_KEY))
worker_app = firebase_admin.initialize_app(worker_cred, name="worker")
worker_db = firestore.client(worker_app)

# =========================================================
# CHECK REGISTER
# =========================================================
@app.route("/check-register", methods=["POST"])
def check_register():

    try:
        body = request.get_json()
        user_id = body.get("user_id")

        doc = worker_db.collection("users") \
            .document(user_id) \
            .collection("dataregister") \
            .document("profile") \
            .get()

        return jsonify({"registered": doc.exists})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"registered": False})

# =========================================================
# REGISTER PAGE
# =========================================================
@app.route("/register-page")
def register_page():
    return render_template("register.html")

# =========================================================
# REGISTER API (FIXED JSON ONLY)
# =========================================================
@app.route("/register", methods=["POST"])
def register():

    try:
        body = request.get_json()

        user_id = body.get("userId")

        worker_db.collection("users") \
            .document(user_id) \
            .collection("dataregister") \
            .document("profile") \
            .set({
                "displayName": body.get("displayName"),
                "pictureUrl": body.get("pictureUrl"),
                "name": body.get("name"),
                "home": body.get("home"),
                "phone": body.get("phone"),
                "address": body.get("address"),
                "workerId": body.get("workerId")
            })

        return jsonify({"status": "ok"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})

# =========================================================
# WEBHOOK
# =========================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    return jsonify({
        "status": "received",
        "server": SERVER_ID
    })

# =========================================================
# HOME
# =========================================================
@app.route("/")
def home():
    return jsonify({"status": "worker online"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)