from flask import Flask, request, jsonify

import os
import json
import traceback
import threading
import time
import requests

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
# ENV
# =========================================================
HUB_FIREBASE_KEY = os.environ.get(
    "HUB_FIREBASE_KEY"
)

WORKER_FIREBASE_KEY = os.environ.get(
    "WORKER_FIREBASE_KEY"
)

SERVER_ID = os.environ.get(
    "SERVER_ID"
)

WORKER_WEBHOOK_URL = os.environ.get(
    "WORKER_WEBHOOK_URL"
)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LINE_CHANNEL_ACCESS_TOKEN"
)

BUCKET_NAME = os.environ.get(
    "BUCKET_NAME"
)

# =========================================================
# CHECK ENV
# =========================================================
required_envs = [

    HUB_FIREBASE_KEY,
    WORKER_FIREBASE_KEY,
    SERVER_ID,
    WORKER_WEBHOOK_URL,
    LINE_CHANNEL_ACCESS_TOKEN,
    BUCKET_NAME
]

if not all(required_envs):

    raise RuntimeError(
        "Missing ENV"
    )

# =========================================================
# INIT HUB FIREBASE
# =========================================================
hub_cred = credentials.Certificate(
    json.loads(HUB_FIREBASE_KEY)
)

hub_app = firebase_admin.initialize_app(

    hub_cred,

    name="hub"
)

# =========================================================
# INIT WORKER FIREBASE
# =========================================================
worker_cred = credentials.Certificate(
    json.loads(WORKER_FIREBASE_KEY)
)

worker_app = firebase_admin.initialize_app(

    worker_cred,

    {
        "storageBucket":
            BUCKET_NAME
    },

    name="worker"
)

# =========================================================
# FIRESTORE
# =========================================================
hub_db = firestore.client(
    hub_app
)

worker_db = firestore.client(
    worker_app
)

bucket = storage.bucket(
    app=worker_app
)

# =========================================================
# HEARTBEAT
# =========================================================
def update_heartbeat():

    while True:

        try:

            hub_db.collection("hub_system") \
                  .document("server_pool") \
                  .collection("servers") \
                  .document(SERVER_ID) \
                  .set({

                      "server_id":
                          SERVER_ID,

                      "status":
                          "online",

                      "health":
                          "good",

                      "cloud_url":
                          WORKER_WEBHOOK_URL,

                      "load_score":
                          0,

                      "last_ping":
                          firestore.SERVER_TIMESTAMP

                  }, merge=True)

            print(
                f"[{SERVER_ID}] heartbeat updated"
            )

        except Exception as e:

            print(str(e))

        time.sleep(30)

heartbeat_thread = threading.Thread(

    target=update_heartbeat,

    daemon=True
)

heartbeat_thread.start()

# =========================================================
# DOWNLOAD IMAGE
# =========================================================
def download_line_image(message_id):

    url = (
        "https://api-data.line.me/v2/bot/message/"
        f"{message_id}/content"
    )

    headers = {

        "Authorization":
            f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    response = requests.get(

        url,

        headers=headers
    )

    if response.status_code != 200:

        raise Exception(
            response.text
        )

    return response.content

# =========================================================
# UPLOAD IMAGE
# =========================================================
def upload_image(image_bytes, filename):

    blob = bucket.blob(
        f"line_images/{filename}"
    )

    blob.upload_from_string(

        image_bytes,

        content_type="image/jpeg"
    )

    blob.make_public()

    return blob.public_url

# =========================================================
# REPLY LINE
# =========================================================
def reply_line(reply_token, text):

    url = (
        "https://api.line.me/v2/bot/message/reply"
    )

    headers = {

        "Authorization":
            f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",

        "Content-Type":
            "application/json"
    }

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

    response = requests.post(

        url,

        headers=headers,

        json=payload
    )

    print(response.text)

# =========================================================
# HOME
# =========================================================
@app.route("/")
def home():

    return f"WORKER : {SERVER_ID}"

# =========================================================
# WORKER WEBHOOK
# =========================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    try:

        body = request.get_json()

        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))

        request_id = body.get(
            "request_id"
        )

        payload = body.get(
            "payload",
            {}
        )

        events = payload.get(
            "events",
            []
        )

        saved_count = 0

        for event in events:

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

            message = event.get(
                "message",
                {}
            )

            message_type = message.get(
                "type"
            )

            message_id = message.get(
                "id"
            )

            text = None

            image_url = None

            # =============================================
            # TEXT
            # =============================================
            if message_type == "text":

                text = message.get(
                    "text"
                )

            # =============================================
            # IMAGE
            # =============================================
            elif message_type == "image":

                image_bytes = download_line_image(
                    message_id
                )

                filename = (
                    f"{request_id}_{message_id}.jpg"
                )

                image_url = upload_image(

                    image_bytes,
                    filename
                )

            # =============================================
            # SAVE FIRESTORE
            # =============================================
            worker_db.collection("line_messages") \
                     .add({

                         "request_id":
                             request_id,

                         "server_id":
                             SERVER_ID,

                         "user_id":
                             user_id,

                         "message_type":
                             message_type,

                         "text":
                             text,

                         "image_url":
                             image_url,

                         "created_at":
                             firestore.SERVER_TIMESTAMP
                     })

            saved_count += 1

            # =============================================
            # REPLY LINE
            # =============================================
            if reply_token:

                if image_url:

                    reply_text = (
                        "บันทึกสำเร็จ\n\n"
                        f"{image_url}"
                    )

                else:

                    reply_text = (
                        "บันทึกข้อความสำเร็จ"
                    )

                reply_line(

                    reply_token,

                    reply_text
                )

        return jsonify({

            "status":
                "success",

            "saved_count":
                saved_count
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
# HEALTH
# =========================================================
@app.route("/health")
def health():

    return jsonify({

        "status":
            "online",

        "server_id":
            SERVER_ID
    })

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get("PORT", 8080)
        ),

        debug=True
    )