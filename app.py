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
FIREBASE_SERVICE_KEY = os.environ.get(
    "FIREBASE_SERVICE_KEY"
)

SERVER_ID = os.environ.get(
    "SERVER_ID"
)

WORKER_WEBHOOK_URL = os.environ.get(
    "WORKER_WEBHOOK_URL"
)

if not FIREBASE_SERVICE_KEY:

    raise RuntimeError(
        "Missing FIREBASE_SERVICE_KEY"
    )

if not SERVER_ID:

    raise RuntimeError(
        "Missing SERVER_ID"
    )

# =========================================================
# FIREBASE INIT
# =========================================================
cred = credentials.Certificate(
    json.loads(FIREBASE_SERVICE_KEY)
)

firebase_admin.initialize_app(
    cred
)

# =========================================================
# FIRESTORE
# =========================================================
db = firestore.client()

# =========================================================
# HEARTBEAT
# =========================================================
def update_heartbeat():

    while True:

        try:

            db.collection("hub_system") \
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
# START THREAD
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

        # =================================================
        # SAVE CURRENT DEVICE
        # =================================================
        db.collection("devices") \
          .document("esp32_001") \
          .set({

              "temperature":
                  payload.get("temperature"),

              "humidity":
                  payload.get("humidity"),

              "updated_at":
                  firestore.SERVER_TIMESTAMP

          }, merge=True)

        # =================================================
        # SAVE LOG HISTORY
        # =================================================
        db.collection("device_logs") \
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