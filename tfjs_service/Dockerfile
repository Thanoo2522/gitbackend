# Dockerfile สำหรับ service แยกที่ทำหน้าที่ convert SavedModel -> TensorFlow.js เท่านั้น
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 tfjs_service:app
