import os
import json
import traceback
import requests
from flask import Flask, request, jsonify, render_template
import firebase_admin
from firebase_admin import credentials, storage, firestore

app = Flask(__name__)

# -----------------------------------
# Configuration & Environment Variables
# ------------------------------------
RTD_URL1 = "https://firebasedatabase.app"
BUCKET_NAME = "gs://basework-51f3b.firebasestorage.app"

WORKER_FIREBASE_KEY = os.environ.get("WORKER_FIREBASE_KEY")
LIFF_ID = os.environ.get("LIFF_ID")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") 

if not WORKER_FIREBASE_KEY:
    raise RuntimeError("Missing WORKER_FIREBASE_KEY")
if not LIFF_ID:
    raise RuntimeError("Missing LIFF_ID")

# Initialize Firebase Admin SDK
cred = credentials.Certificate(json.loads(WORKER_FIREBASE_KEY))
firebase_admin.initialize_app(
    cred,
    {
        "storageBucket": BUCKET_NAME,
        "databaseURL": RTD_URL1
    }
)

db = firestore.client()
bucket = storage.bucket()


# ------------------------------------
# 🚀 1. LINE OA WEBHOOK ENDPOINT
# ------------------------------------
@app.route('/worker-webhook', methods=['POST'])
def worker_webhook():
    try:
        body = request.get_json()
        events = body.get('events', [])

        for event in events:
            # ตรวจสอบ Event ข้อความ และคัดกรองเฉพาะห้องแชทที่มี userId
            if event.get('type') == 'message' and 'replyToken' in event:
                reply_token = event['replyToken']
                user_id = event['source'].get('userId')
                
                if not user_id:
                    continue

                # เช็คประวัติใน Firestore: user/{userId}
                user_ref = db.collection('user').document(user_id)
                user_doc = user_ref.get()

                # สร้างลิงก์ยิงตรงเข้าเว็บลงทะเบียนของระบบเราเอง
                register_url = f"https://run.app{user_id}?ofm=default_shop"

                if not user_doc.exists:
                    # กรณี "ยังไม่ลงทะเบียน" -> ส่งข้อความพร้อมลิงก์ไปหน้า register.html
                    text_msg = f"สวัสดีค่ะ คุณยังไม่ได้ลงทะเบียนเข้าใช้งานระบบ กรุณาคลิกลิงก์ด้านล่างเพื่อสมัครสมาชิกก่อนนะคะ\n\n👇 คลิกเพื่อลงทะเบียน\n{register_url}"
                else:
                    # กรณี "ลงทะเบียนแล้ว" -> ดึงข้อมูลมาทักทายตอบกลับ
                    user_data = user_doc.to_dict()
                    user_name = user_data.get('name', 'สมาชิก')
                    text_msg = f"สวัสดีค่ะคุณ {user_name} ยินดีต้อนรับกลับเข้าสู่ระบบค่ะ มีอะไรให้ฉันช่วยไหมคะ?"

                # ยิง Reply Message กลับหาผู้ใช้
                send_line_reply(reply_token, text_msg)

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500


def send_line_reply(reply_token, text_msg):
    """ฟังก์ชันส่ง HTTP POST Message กลับไปยัง LINE API"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("❌ Error: Missing LINE_CHANNEL_ACCESS_TOKEN env variable.")
        return

    url = "https://line.me"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text_msg
            }
        ]
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f"LINE Reply Status: {response.status_code}, Response: {response.text}")


# ------------------------------------
# 🌐 2. WEB REGISTRATION ROUTES
# ------------------------------------

@app.route('/user/<user_id>', methods=['GET'])
def check_user(user_id):
    """ตรวจสอบ path /user/{UserID} ถ้าไม่มีเปิดหน้า register.html"""
    try:
        user_ref = db.collection('user').document(user_id)
        user_doc = user_ref.get()

        if user_doc.exists:
            # หากมีข้อมูลใน Firestore อยู่แล้ว ส่งค่าข้อมูลผู้ใช้กลับไปเป็น JSON
            return jsonify({"status": "exists", "data": user_doc.to_dict()}), 200
        else:
            # หากไม่มีข้อมูลในระบบ -> เรนเดอร์หน้าจอลงทะเบียนสมัครสมาชิก พร้อมส่ง LIFF_ID ไปใช้งาน
            return render_template('register.html', liff_id=LIFF_ID)
            
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/register', methods=['POST'])
def register_user():
    """API รับ Payload จากหน้าเว็บเพื่อลงบันทึกข้อมูลลง Firestore"""
    try:
        data = request.json
        user_id = data.get('userId')
        ofm = data.get('ofm')
        name = data.get('name')
        home = data.get('home')
        address = data.get('address')
        phone = data.get('phone')

        if not user_id or not name or not home or not address or not phone:
            return jsonify({"status": "error", "message": "ข้อมูลไม่ครบถ้วน"}), 400

        # บันทึกข้อมูลลงเอกสาร Firestore ใน Path: user/{user_id}
        user_ref = db.collection('user').document(user_id)
        user_ref.set({
            "ofm": ofm,
            "name": name,
            "home": home,
            "address": address,
            "phone": phone,
            "registered_at": firestore.SERVER_TIMESTAMP
        })

        return jsonify({"status": "ok", "message": "ลงทะเบียนสำเร็จ"}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/config/<ofm>', methods=['GET'])
def get_config(ofm):
    """ส่ง API endpoint ปลายทางกลับให้หน้าเว็บ LIFF ทราบตำแหน่งส่งข้อมูล"""
    root_url = request.url_root.rstrip('/')
    return jsonify({
        "status": "ok",
        "apiUrl": f"{root_url}/api/register"
    }), 200


if __name__ == '__main__':
    # เปิดการทำงานโหมด Debug ทดสอบที่พอร์ต 5000 
    app.run(debug=True, port=5000)
