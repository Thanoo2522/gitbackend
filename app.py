from itertools import product
from flask import Flask, request, jsonify,Response , stream_with_context,render_template
import os, json, io, traceback
import requests
from io import BytesIO
import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore, messaging

from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import time
from datetime import datetime
#from flask_cors import CORS
import urllib.parse # สำหรับถอดรหัสภาษาไทย
 
 
 
 
 

 
# ---------------------------- 
# Flask
# ---------------------------------
app = Flask(__name__)
#CORS(app, resources={r"/*": {"origins": "*"}})
# ------------------------------------
# Firebase Config
# ------------------------------------
RTD_URL1 = "https://bestofm-a31a0-default-rtdb.asia-southeast1.firebasedatabase.app/"
BUCKET_NAME = "bestofm-a31a0.firebasestorage.app"
#----------------------
service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
if not service_account_json:
    raise RuntimeError("Missing FIREBASE_SERVICE_KEY")

cred = credentials.Certificate(json.loads(service_account_json))
#-----------------------
firebase_admin.initialize_app(
    cred,
    {
        "storageBucket": BUCKET_NAME,
        "databaseURL": RTD_URL1
    }
)

# ✅ ใช้ Firebase Admin เท่านั้น (ไม่มี ADC)
db = firestore.client()
rtdb_ref = rtdb.reference("/")
bucket = storage.bucket()


# ------------------------------ 
if __name__ == "__main__":
    app.run(debug=True)
