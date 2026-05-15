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
# FIREBASE
# ======================================================
hub_cred = credentials.Certificate(
    json.loads(HUB_FIREBASE_KEY)
)

hub_app = firebase_admin.initialize_app(
    hub_cred,
    name="hub"
)

worker_cred = credentials.Certificate(
    json.loads(WORKER_FIREBASE_KEY)
)

worker_app = firebase_admin.initialize_app(
    worker_cred,
    name="worker"
)

hub_db = firestore.client(hub_app)

worker_db = firestore.client(worker_app)

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

                      "cloud_url":
                          WORKER_WEBHOOK_URL,

                      "load_score":
                          0,

                      "last_ping":
                          firestore.SERVER_TIMESTAMP

                  }, merge=True)

        except Exception as e:

            print(str(e))

        time.sleep(30)

threading.Thread(
    target=update_heartbeat,
    daemon=True
).start()

# =========================================================
# CHECK REGISTER
# =========================================================
@app.route("/check-register", methods=["POST"])
def check_register():

    try:

        body = request.get_json()

        user_id = body.get(
            "user_id"
        )

        doc = worker_db.collection("users") \
                       .document(user_id) \
                       .collection("dataregister") \
                       .document("profile") \
                       .get()

        return jsonify({

            "registered":
                doc.exists
        })

    except Exception as e:

        return jsonify({

            "registered":
                False,

            "message":
                str(e)
        })

# =========================================================
# REGISTER
# =========================================================
@app.route("/register", methods=["POST"])
def register():

    try:

        body = request.get_json()

        user_id = body.get(
            "userId"
        )

        worker_db.collection("users") \
                 .document(user_id) \
                 .collection("dataregister") \
                 .document("profile") \
                 .set({

                     "displayName":
                         body.get("displayName"),

                     "pictureUrl":
                         body.get("pictureUrl"),

                     "name":
                         body.get("name"),

                     "phone":
                         body.get("phone"),

                     "address":
                         body.get("address"),

                     "workerId":
                         body.get("workerId"),

                     "created_at":
                         firestore.SERVER_TIMESTAMP
                 })

        return jsonify({

            "status":
                "ok"
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
# WORKER WEBHOOK
# =======================================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():

    return jsonify({

        "status":
            "received",

        "server":
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
        )
    )