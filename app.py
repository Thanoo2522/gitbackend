from flask import Flask, request, jsonify

import os
import json
import traceback
import requests
import time

from datetime import datetime

import firebase_admin

from firebase_admin import (
    credentials,
    firestore
)

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)

# =========================================================
# ENV
# =========================================================
WORKER_FIREBASE_KEY = os.environ.get(
    "WORKER_FIREBASE_KEY"
)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LINE_CHANNEL_ACCESS_TOKEN"
)

LIFF_ID = os.environ.get(
    "LIFF_ID"
)

SERVER_ID = os.environ.get(
    "SERVER_ID"
)

# =========================================================
# FIREBASE
# =========================================================
worker_cred = credentials.Certificate(
    json.loads(WORKER_FIREBASE_KEY)
)

worker_app = firebase_admin.initialize_app(
    worker_cred,
    name="worker"
)

worker_db = firestore.client(worker_app)

# =========================================================
# LINE
# =========================================================
LINE_REPLY_API = (
    "https://api.line.me/v2/bot/message/reply"
)

LINE_PUSH_API = (
    "https://api.line.me/v2/bot/message/push"
)

LINE_HEADERS = {

    "Authorization":
        f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",

    "Content-Type":
        "application/json"
}

# =========================================================
# HOME
# =========================================================
@app.route("/")
def home():

    return f"{SERVER_ID} RUNNING"

# =========================================================
# CHECK REGISTER
# =========================================================
@app.route(
    "/check-register",
    methods=["POST"]
)
def check_register():

    try:

        body = request.get_json()

        user_id = body.get(
            "user_id"
        )

        if not user_id:

            return jsonify({

                "registered":
                    False
            })

        doc = (
            worker_db
            .collection("user")
            .document(user_id)
            .get()
        )

        if not doc.exists:

            return jsonify({

                "registered":
                    False
            })

        data = doc.to_dict()

        register = data.get(
            "register",
            False
        )

        return jsonify({

            "registered":
                register
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "registered":
                False,

            "message":
                str(e)
        })

# =========================================================
# REPLY MESSAGE
# =========================================================
def reply_message(
    reply_token,
    text
):

    payload = {

        "replyToken":
            reply_token,

        "messages": [

            {
                "type":
                    "text",

                "text":
                    text
            }
        ]
    }

    r = requests.post(

        LINE_REPLY_API,

        headers=LINE_HEADERS,

        json=payload,

        timeout=10
    )

    print(r.status_code)
    print(r.text)

# =========================================================
# PUSH MESSAGE
# =========================================================
def push_message(
    user_id,
    text
):

    payload = {

        "to":
            user_id,

        "messages": [

            {
                "type":
                    "text",

                "text":
                    text
            }
        ]
    }

    r = requests.post(

        LINE_PUSH_API,

        headers=LINE_HEADERS,

        json=payload,

        timeout=10
    )

    print(r.status_code)
    print(r.text)

# =========================================================
# WORKER WEBHOOK
# =========================================================
@app.route(
    "/worker-webhook",
    methods=["POST"]
)
def worker_webhook():

    try:

        body = request.get_json()

        print("=" * 50)
        print("WORKER WEBHOOK")
        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))
        print("=" * 50)

        events = body.get(
            "events",
            []
        )

        for event in events:

            event_type = event.get(
                "type"
            )

            reply_token = event.get(
                "replyToken"
            )

            source = event.get(
                "source",
                {}
            )

            user_id = source.get(
                "userId"
            )

            if not user_id:
                continue

            # =============================================
            # GET USER
            # =============================================
            user_doc = (
                worker_db
                .collection("user")
                .document(user_id)
                .get()
            )

            if not user_doc.exists:

                continue

            user_data = user_doc.to_dict()

            fullname = user_data.get(
                "fullname",
                "Unknown"
            )

            # =============================================
            # MESSAGE EVENT
            # =============================================
            if event_type == "message":

                message = event.get(
                    "message",
                    {}
                )

                message_type = message.get(
                    "type"
                )

                # =========================================
                # TEXT
                # =========================================
                if message_type == "text":

                    text = message.get(
                        "text",
                        ""
                    )

                    # SAVE CHAT LOG
                    worker_db.collection(
                        "chat_logs"
                    ).add({

                        "user_id":
                            user_id,

                        "fullname":
                            fullname,

                        "text":
                            text,

                        "timestamp":
                            datetime.utcnow()
                    })

                    # =====================================
                    # COMMAND
                    # =====================================
                    if text == "ping":

                        reply_message(

                            reply_token,

                            "pong"
                        )

                    elif text == "profile":

                        profile_text = (

                            f"ชื่อ: {fullname}\n"
                            f"USER ID:\n{user_id}"
                        )

                        reply_message(

                            reply_token,

                            profile_text
                        )

                    else:

                        reply_text = (

                            f"สวัสดี {fullname}\n\n"
                            f"ข้อความของคุณ:\n{text}"
                        )

                        reply_message(

                            reply_token,

                            reply_text
                        )

            # =============================================
            # FOLLOW EVENT
            # =============================================
            elif event_type == "follow":

                push_message(

                    user_id,

                    (
                        "ยินดีต้อนรับ\n"
                        "ระบบพร้อมใช้งานแล้ว"
                    )
                )

        return jsonify({

            "status":
                "success"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)
        }), 500

# =========================================================
# REGISTER USER
# =========================================================
@app.route(
    "/register-user",
    methods=["POST"]
)
def register_user():

    try:

        body = request.get_json()

        user_id = body.get(
            "userId"
        )

        if not user_id:

            return jsonify({

                "status":
                    "error",

                "message":
                    "no userId"
            })

        save_data = {

            "userId":
                user_id,

            "displayName":
                body.get("displayName"),

            "pictureUrl":
                body.get("pictureUrl"),

            "fullname":
                body.get("fullname"),

            "phone":
                body.get("phone"),

            "worker":
                body.get("worker"),

            "register":
                True,

            "created_at":
                datetime.utcnow()
        }

        (
            worker_db
            .collection("user")
            .document(user_id)
            .set(save_data)
        )

        return jsonify({

            "status":
                "success",

            "message":
                "ลงทะเบียนสำเร็จ"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)
        }), 500

# =========================================================
# HEARTBEAT
# =========================================================
@app.route(
    "/heartbeat",
    methods=["POST"]
)
def heartbeat():

    try:

        body = request.get_json()

        cpu = body.get("cpu", 0)
        ram = body.get("ram", 0)

        worker_db.collection(
            "worker_status"
        ).document(SERVER_ID).set({

            "server_id":
                SERVER_ID,

            "status":
                "online",

            "cpu":
                cpu,

            "ram":
                ram,

            "last_heartbeat":
                int(time.time())
        })

        return jsonify({

            "status":
                "success"
        })

    except Exception as e:

        traceback.print_exc()

        return jsonify({

            "status":
                "error",

            "message":
                str(e)
        })

# =========================================================
# RUN
# =========================================================
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