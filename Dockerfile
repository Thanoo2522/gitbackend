# 1. ใช้ Python 3.11 แบบ slim เพื่อประหยัดพื้นที่และรันไว
FROM python:3.11-slim

# 2. ตั้งค่าโฟลเดอร์ทำงานในเครื่อง Server
WORKDIR /app

# 3. ก๊อปปี้ไฟล์รายการ Library ไปติดตั้งก่อน
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. ก๊อปปี้โค้ดทั้งหมด (รวมถึง app.py) เข้าไปใน Server
COPY . .

# 5. สั่งรัน Flask ด้วย Gunicorn (ตัวนี้เสถียรกว่ารัน python app.py ตรงๆ)
# Cloud Run จะส่งค่า PORT มาให้ทาง Environment Variable เอง
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
#--