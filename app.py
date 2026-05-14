from flask import Flask, request, jsonify

import os
import json
import traceback
import threading
import time

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

# =========================================================
# CHECK ENV
# =========================================================
if not HUB_FIREBASE_KEY:

    raise RuntimeError(
        "Missing HUB_FIREBASE_KEY"
    )

if not WORKER_FIREBASE_KEY:

    raise RuntimeError(
        "Missing WORKER_FIREBASE_KEY"
    )

if not SERVER_ID:

    raise RuntimeError(
        "Missing SERVER_ID"
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

# =========================================================
# START HEARTBEAT
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

        temperature = payload.get(
            "temperature"
        )

        humidity = payload.get(
            "humidity"
        )

        # =================================================
        # VALIDATE
        # =================================================
        if temperature is None:

            return jsonify({

                "status":
                    "error",

                "message":
                    "missing temperature"

            }), 400

        # =================================================
        # SAVE CURRENT DEVICE
        # =================================================
        worker_db.collection("devices") \
                 .document("esp32_001") \
                 .set({

                     "temperature":
                         temperature,

                     "humidity":
                         humidity,

                     "updated_at":
                         firestore.SERVER_TIMESTAMP

                 }, merge=True)

        # =================================================
        # SAVE LOG
        # =================================================
        worker_db.collection("device_logs") \
                 .add({

                     "request_id":
                         request_id,

                     "payload":
                         payload,

                     "server_id":
                         SERVER_ID,

                     "created_at":
                         firestore.SERVER_TIMESTAMP
                 })

        print("SAVE FIRESTORE SUCCESS")

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