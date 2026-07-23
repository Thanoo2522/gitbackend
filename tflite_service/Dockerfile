FROM python:3.11-slim

WORKDIR /app

# libgl/libglib ที่ onnx2tf/opencv (dependency ของ onnx2tf) ต้องใช้ตอน import
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 0 app:app
