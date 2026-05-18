from flask import Flask, request, jsonify

import os
import json
import traceback
import requests
import time
import threading

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

WORKER_WEBHOOK_URL = os.environ.get(
    "WORKER_WEBHOOK_URL"
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
# LINE API
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
# HEARTBEAT LOOP
# =========================================================
def heartbeat_loop():

    while True:

        try:

            save_data = {

                "server_id":
                    SERVER_ID,

                "status":
                    "online",

                "cpu":
                    10,

                "ram":
                    20,

                "load_score":
                    5,

                "cloud_url":
                    WORKER_WEBHOOK_URL,

                "last_heartbeat":
                    int(time.time())
            }

            (
                worker_db
                .collection("hub_system")
                .document("server_pool")
                .collection("servers")
                .document(SERVER_ID)
                .set(save_data, merge=True)
            )

            print("=" * 50)
            print("HEARTBEAT SENT")
            print(json.dumps(
                save_data,
                indent=2,
                ensure_ascii=False
            ))
            print("=" * 50)

        except Exception:
            traceback.print_exc()

        time.sleep(30)

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

        print("=" * 50)
        print("CHECK REGISTER")
        print(body)
        print("=" * 50)

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

        registered = data.get(
            "register",
            False
        )

        return jsonify({

            "registered":
                registered
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

    print("=" * 50)
    print("REPLY MESSAGE")
    print(r.status_code)
    print(r.text)
    print("=" * 50)

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

    print("=" * 50)
    print("PUSH MESSAGE")
    print(r.status_code)
    print(r.text)
    print("=" * 50)

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

            # =========================================
            # GET USER
            # =========================================
            user_doc = (
                worker_db
                .collection("user")
                .document(user_id)
                .get()
            )

            if not user_doc.exists:

                print("USER NOT FOUND")

                continue

            user_data = user_doc.to_dict()

            fullname = user_data.get(
                "fullname",
                "Unknown"
            )

            # =========================================
            # MESSAGE EVENT
            # =========================================
            if event_type == "message":

                message = event.get(
                    "message",
                    {}
                )

                message_type = message.get(
                    "type"
                )

                # =====================================
                # TEXT MESSAGE
                # =====================================
                if message_type == "text":

                    text = message.get(
                        "text",
                        ""
                    )

                    print("TEXT =", text)

                    # =================================
                    # SAVE CHAT LOG
                    # =================================
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

                    # =================================
                    # COMMANDS
                    # =================================
                    if text.lower() == "ping":

                        reply_message(

                            reply_token,

                            "pong"
                        )

                    elif text.lower() == "profile":

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

            # =========================================
            # FOLLOW EVENT
            # =========================================
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

        print("=" * 50)
        print("REGISTER USER")
        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))
        print("=" * 50)

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
# RUN
# =========================================================
if __name__ == "__main__":

    threading.Thread(

        target=heartbeat_loop,

        daemon=True

    ).start()

    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                8080
            )
        )
    )