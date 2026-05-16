import os
import json
import requests
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# 📌 เริ่มต้นเชื่อมต่อฐานข้อมูล Firebase 
if not firebase_admin._apps:
    firebase_admin.initialize_app()
db = firestore.client()

# 📌 กำหนดค่าตัวแปรกลางตามที่คุณให้มา
LIFF_URL = "https://liff.line.me/2010064672-zx1EQTMH"
CHANNEL_ACCESS_TOKEN = "WFrlScHP1ovaJI4au3oJS6X61jmv1BmeG+HoBs0bD8FRLnbNxk1/OgWCSf+MzM3BYrfPA9og9AA5jqHTj0iQ1z5N/qEWFlrHA7BpJS/9/+Sb7MP6XX+QssXcRMAEdysVLM+NWsxdhuaVrueRLLJElwdB04t89/1O/w1cDnyilFU="  # 👈 นำ Token ของคุณมาใส่ตรงนี้
DEFAULT_OFM = "default_shop"

# ==========================================
# 🛑 ROUTE 1: WORKER WEBHOOK (ตัวตรวจเช็คสิทธิ์)
# ==========================================
@app.route("/worker-webhook", methods=["POST"])
def worker_webhook():
    try:
        body = request.get_data()
        body_json = json.loads(body)
        events = body_json.get("events", [])

        for event in events:
            user_id = event["source"].get("userId")
            reply_token = event.get("replyToken")
            
            if not user_id or not reply_token:
                continue

            # 🔍 ตรวจสอบเอกสารข้อมูลสมาชิกที่เส้นทาง users/{userID}
            user_ref = db.collection("users").document(user_id)
            user_doc = user_ref.get()

            # ❌ กรณีไม่มีข้อมูลในฐานข้อมูล -> ส่งลิงก์บังคับสมัครสมาชิกทันที
            if not user_doc.exists:
                print(f"🛑 ไม่พบรหัสผู้ใช้: {user_id} ในระบบ กำลังส่งลิงก์สมัครสมาชิก...")
                
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
                }
                
                # แนบตัวแปรไปกับลิงก์ LIFF เพื่อให้หน้าเว็บนำไปประมวลผลต่อ
                registration_link = f"{LIFF_URL}?ofm={DEFAULT_OFM}"

                # ส่งการแจ้งเตือนกลับเป็นรูปปุ่มกด Flex Message
                requests.post(
                    "https://line.me",
                    headers=headers,
                    json={
                        "replyToken": reply_token,
                        "messages": [
                            {
                                "type": "flex",
                                "altText": "กรุณาลงทะเบียนสมาชิกก่อนใช้งานค่ะ",
                                "contents": {
                                    "type": "bubble",
                                    "body": {
                                        "type": "box",
                                        "layout": "vertical",
                                        "contents": [
                                            {"type": "text", "text": "🔒 ตรวจพบผู้ใช้งานใหม่", "weight": "bold", "size": "xl", "color": "#ff3333"},
                                            {"type": "text", "text": "คุณยังไม่ได้ลงทะเบียนสิทธิ์ใช้งาน กรุณากดปุ่มด้านล่างเพื่อยืนยันตัวตนก่อนนะคะ", "margin": "md", "wrap": True}
                                        ]
                                    },
                                    "footer": {
                                        "type": "box",
                                        "layout": "vertical",
                                        "contents": [
                                            {
                                                "type": "button",
                                                "style": "primary",
                                                "color": "#06c755",
                                                "action": {
                                                    "type": "uri",
                                                    "label": "สมัครสมาชิกคลิกที่นี่",
                                                    "uri": registration_link
                                                }
                                            }
                                        ]
                                    }
                                }
                            }
                        ]
                    }
                )
                continue  # บล็อกคำสั่งถัดไปเพื่อรอบังคับสมัครสมาชิกให้เสร็จก่อนเท่านั้น

            # ====== ผ่านด่านลงทะเบียนแล้ว -> รันระบบตอบกลับปกติของคุณต่อจากตรงนี้ ======
            print(f"✅ บัญชี {user_id} ได้รับอนุญาตให้ใช้งานระบบแล้ว")

        return "OK", 200
    except Exception as e:
        print("💥 Error:", str(e))
        return "Internal Error", 500

# ==========================================
# 💾 ROUTE 2: REGISTER API (ตัวบันทึกตอนสมัครเสร็จ)
# ==========================================
@app.route("/api/register-submit", methods=["POST"])
def register_submit():
    try:
        data = request.get_json()
        user_id = data.get("userId")
        
        if not user_id:
            return jsonify({"status": "fail", "message": "Missing userId"}), 400

        # เขียนชุดบันทึกข้อมูลเข้าสู่ฐานข้อมูล Firestore ที่ users/{userID} โดยตรง
        db.collection("users").document(user_id).set({
            "ofm": data.get("ofm"),
            "name": data.get("name"),
            "home": data.get("home"),
            "address": data.get("address"),
            "phone": data.get("phone"),
            "registered_at": firestore.SERVER_TIMESTAMP
        })
        
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "fail", "message": str(e)}), 500

# API ตัวอย่างเพื่อจำลองส่ง Config ให้หน้า LIFF
@app.route("/config/<ofm_name>", methods=["GET"])
def get_config(ofm_name):
    return jsonify({
        "status": "ok",
        "apiUrl": "https://run.app"
    })

if __name__ == "__main__":
    app.run(port=5000, debug=True)
