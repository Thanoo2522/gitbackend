from flask import Flask, request, jsonify

import os
import json
import traceback
import requests
import threading
import time

import firebase_admin

from firebase_admin import (
    credentials,
    firestore,
    db as rtdb,
    storage
)

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)

# =========================================================
# ENV
# =========================================================
FIREBASE_SERVICE_KEY = os.environ.get(
    "FIREBASE_SERVICE_KEY"
)

SERVER_ID = os.environ.get(
    "SERVER_ID"
)

if not FIREBASE_SERVICE_KEY:

    raise RuntimeError(
        "Missing FIREBASE_SERVICE_KEY"
    )

if not SERVER_ID:

    raise RuntimeError(
        "Missing SERVER_ID"
    )

# =======================================================
# INIT HUB FIREBASE
# =========================================================
hub_cred = credentials.Certificate(
    json.loads(FIREBASE_SERVICE_KEY)
)

hub_app = firebase_admin.initialize_app(

    hub_cred,

    name="hub"
)

# =========================================================
# HUB FIRESTORE
# =========================================================
hub_db = firestore.client(
    hub_app
)

# =========================================================
# CACHE
# =========================================================
tenant_apps = {}

# =========================================================
# LOAD TENANT FIREBASE
# =========================================================
def get_tenant_app():

    global tenant_apps

    # =====================================================
    # CACHE
    # =====================================================
    if SERVER_ID in tenant_apps:

        return tenant_apps[SERVER_ID]

    # =====================================================
    # READ CONFIG
    # ================================================= 
    doc_ref = (

        hub_db.collection("hub_system")
              .document("server_pool")
              .collection("servers")
              .document(SERVER_ID)
    )

    doc = doc_ref.get()

    if not doc.exists:

        raise Exception(
            "worker config not found"
        )

    data = doc.to_dict()

    # =====================================================
    # CONFIG
    # =====================================================
    firebase_key = data.get(
        "FIREBASE_SERVICE_KEY"
    )

    rtdb_url = data.get(
        "RTDB_URL"
    )

    bucket_name = data.get(
        "BUCKET_NAME"
    )

    # =====================================================
    # FIREBASE CERT
    # =====================================================
    cred = credentials.Certificate(
        json.loads(firebase_key)
    )

    # =====================================================
    # INIT APP
    # =====================================================
    try:

        tenant_app = firebase_admin.get_app(
            SERVER_ID
        )

    except ValueError:

        tenant_app = firebase_admin.initialize_app(

            cred,

            {
                "databaseURL":
                    rtdb_url,

                "storageBucket":
                    bucket_name
            },

            name=SERVER_ID
        )

    tenant_apps[SERVER_ID] = tenant_app

    return tenant_app

# =========================================================
# UPDATE HEARTBEAT
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

                      "last_ping":
                          firestore.SERVER_TIMESTAMP,

                      "cloud_url":
                          os.environ.get(
                              "WORKER_WEBHOOK_URL"
                          ),

                      "load_score":
                          0

                  }, merge=True)

            print(
                f"[{SERVER_ID}] heartbeat updated"
            )

        except Exception as e:

            print("heartbeat error")
            print(str(e))

        time.sleep(30)

# =========================================================
# START HEARTBEAT THREAD
# =========================================================
heartbeat_thread = threading.Thread(
    target=update_heartbeat,
    daemon=True
)

heartbeat_thread.start()

# =========================================================
# HOME
# =========================================================
@app.route("/")
def home():

    return f"WORKER RUNNING : {SERVER_ID}"

# =========================================================
# WORKER WEBHOOK
# =========================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    try:

        # =================================================
        # BODY
        # =================================================
        body = request.get_json()

        print(json.dumps(
            body,
            indent=2,
            ensure_ascii=False
        ))

        # =================================================
        # LOAD FIREBASE
        # =================================================
        tenant_app = get_tenant_app()

        tenant_db = firestore.client(
            tenant_app
        )

        # =================================================
        # READ CONFIG
        # =================================================
        doc_ref = (

            hub_db.collection("hub_system")
                  .document("server_pool")
                  .collection("servers")
                  .document(SERVER_ID)
        )

        doc = doc_ref.get()

        data = doc.to_dict()

        line_token = data.get(
            "LINE_CHANNEL_ACCESS_TOKEN"
        )

        # =================================================
        # WRITE LOG
        # =================================================
        tenant_db.collection("logs") \
                 .add({

                     "message":
                        "worker processed",

                     "server_id":
                        SERVER_ID,

                     "created_at":
                        firestore.SERVER_TIMESTAMP
                 })

        # =================================================
        # LINE EVENT
        # =================================================
        line_body = body.get(
            "line_body", {}
        )

        events = line_body.get(
            "events", []
        )

        # =================================================
        # REPLY
        # =================================================
        if events:

            reply_token = events[0].get(
                "replyToken"
            )

            headers = {

                "Content-Type":
                    "application/json",

                "Authorization":
                    f"Bearer {line_token}"
            }

            payload = {

                "replyToken":
                    reply_token,

                "messages": [
                    {
                        "type": "text",

                        "text":
                            f"WORKER : {SERVER_ID}"
                    }
                ]
            }

            requests.post(

                "https://api.line.me/v2/bot/message/reply",

                headers=headers,

                json=payload,

                timeout=10
            )

        # =================================================
        # RETURN
        # =================================================
        return jsonify({

            "status":
                "success",

            "server_id":
                SERVER_ID
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
# TEST WRITE
# =========================================================
@app.route("/test-write")
def test_write():

    try:

        tenant_app = get_tenant_app()

        tenant_db = firestore.client(
            tenant_app
        )

        tenant_db.collection("device_logs") \
                 .add({

                     "temperature":
                        32.5,

                     "humidity":
                        70,

                     "device":
                        "ESP32",

                     "created_at":
                        firestore.SERVER_TIMESTAMP
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