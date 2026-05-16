import os
import json
import traceback
import requests

from flask import (
    Flask,
    request,
    jsonify,
    render_template
)

import firebase_admin

from firebase_admin import (
    credentials,
    firestore,
    storage
)

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

# =========================================================
# CONFIG
# =========================================================

BUCKET_NAME = "gs://basework-51f3b.firebasestorage.app"

WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LINE_CHANNEL_ACCESS_TOKEN"
)

LIFF_ID = os.environ.get("LIFF_ID")

if not WORKER_FIREBASE_KEY:
    raise RuntimeError("Missing WORKER_FIREBASE_KEY")

if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN")

if not LIFF_ID:
    raise RuntimeError("Missing LIFF_ID")

# =========================================================
# FIREBASE
# =========================================================

cred = credentials.Certificate(
    json.loads(WORKER_FIREBASE_KEY)
)

firebase_admin.initialize_app(
    cred,
    {
        "storageBucket": BUCKET_NAME
    }
)

db = firestore.client()
bucket = storage.bucket()

# =========================================================
# LINE API
# =========================================================

LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"

LINE_HEADERS = {
    "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# =========================================================
# REGISTER PAGE
# =========================================================

@app.route("/register-page")
def register_page():

    ofm = request.args.get("ofm", "")

    return render_template(
        "register.html",
        LIFF_ID=LIFF_ID,
        OFM=ofm
    )

# =========================================================
# REGISTER API
# =========================================================

@app.route("/register", methods=["POST"])
def register():

    try:

        body = request.get_json()

        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))

        user_id = body.get("userId")

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "missing userId"
            }), 400

        data = {
            "name": body.get("name", ""),
            "home": body.get("home", ""),
            "address": body.get("address", ""),
            "phone": body.get("phone", ""),
            "ofm": body.get("ofm", ""),
            "register_status": True,
            "created_at": firestore.SERVER_TIMESTAMP
        }

        db.collection("user").document(user_id).set(
            data,
            merge=True
        )

        return jsonify({
            "status": "ok"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# =========================================================
# WEBHOOK
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():

    try:

        body = request.get_json()

        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))

        events = body.get("events", [])

        for event in events:

            reply_token = event.get("replyToken")

            source = event.get("source", {})

            user_id = source.get("userId")

            if not user_id:
                continue

            # =================================================
            # CHECK USER REGISTER
            # =================================================

            doc_ref = db.collection("user").document(user_id)

            doc = doc_ref.get()

            # =================================================
            # USER NOT FOUND
            # =================================================

            if not doc.exists:

                register_url = (
                    "https://YOUR_CLOUD_RUN_URL/register-page"
                    "?ofm=testshop"
                )

                payload = {
                    "replyToken": reply_token,
                    "messages": [
                        {
                            "type": "text",
                            "text": (
                                "กรุณาลงทะเบียนก่อนใช้งาน\n"
                                f"{register_url}"
                            )
                        }
                    ]
                }

                requests.post(
                    LINE_REPLY_API,
                    headers=LINE_HEADERS,
                    json=payload
                )

                continue

            # =================================================
            # USER REGISTERED
            # =================================================

            user_data = doc.to_dict()

            payload = {
                "replyToken": reply_token,
                "messages": [
                    {
                        "type": "text",
                        "text": (
                            f"สวัสดี {user_data.get('name', '')}"
                        )
                    }
                ]
            }

            requests.post(
                LINE_REPLY_API,
                headers=LINE_HEADERS,
                json=payload
            )

        return "OK"

    except Exception as e:

        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
        debug=True
    )