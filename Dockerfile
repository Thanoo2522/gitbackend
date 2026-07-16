# 1. ใช้ Python 3.11 แบบ slim เพื่อประหยัดพื้นที่และรันไว
FROM python:3.11-slim

# 2. ตั้งค่าโฟลเดอร์ทำงานในเครื่อง Server
WORKDIR /app

# 2.5 ✅ ติดตั้ง system libraries ที่ opencv-python (ที่ ultralytics เรียกใช้ภายใน) ต้องการ
#     - libgl1        : ให้ libGL.so.1 (แก้ error "libGL.so.1: cannot open shared object file")
#     - libglib2.0-0   : ให้ libgobject/libglib ที่ opencv ต้องใช้ตอน import cv2 เช่นกัน
#     ทำตรงนี้แทนการพยายามบังคับ pip ให้เลือก opencv-python-headless เพราะ ultralytics
#     ประกาศ opencv-python (ตัวเต็ม) เป็น dependency ของตัวเองอยู่ดี ต่อให้เราลง headless
#     ไว้ก่อนใน requirements.txt ก็ตาม สุดท้าย pip resolver มักจะลงตัวเต็มทับอยู่ดี
#     วิธีนี้เลยชัวร์กว่า เพราะแก้ที่ system library ให้รองรับได้ทั้งสองแบบไปเลย
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 3. ก๊อปปี้ไฟล์รายการ Library ไปติดตั้งก่อน
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. ก๊อปปี้โค้ดทั้งหมด (รวมถึง app.py) เข้าไปใน Server
COPY . .

# 5. สั่งรัน Flask ด้วย Gunicorn (ตัวนี้เสถียรกว่ารัน python app.py ตรงๆ)
# Cloud Run จะส่งค่า PORT มาให้ทาง Environment Variable เอง
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app