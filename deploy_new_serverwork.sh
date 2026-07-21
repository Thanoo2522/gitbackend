#!/usr/bin/env bash
# =====================================================================
# deploy_new_serverwork.sh
#
# ใช้สร้าง Cloud Run "serverworkN" ใหม่แบบครบชุด (services + job)
# แล้วลงทะเบียนเข้า Hub Firestore (project BaseLineOA) อัตโนมัติ
#
# วิธีใช้:
#   1) เปิด terminal ที่ root ของ repo "linebackend" (ที่มี app.py,
#      Dockerfile, export_service/, tfjs_service/, gpu_service/ อยู่)
#   2) ./deploy_new_serverwork.sh serverwork2
#
# ก่อนรันครั้งแรก:
#   chmod +x deploy_new_serverwork.sh
#   gcloud auth login
#   pip install firebase-admin --break-system-packages   (สำหรับ register_worker.py)
# =====================================================================
set -euo pipefail

# ---------- CONFIG: แก้ให้ตรงกับของจริง ----------
REGION="asia-southeast1"                  # จะใช้ region เดียวกับ serverwork1 หรือเปลี่ยนก็ได้
BILLING_ACCOUNT_ID="XXXXXX-XXXXXX-XXXXXX"  # ดูได้จาก: gcloud billing accounts list
HUB_FIREBASE_PROJECT="baselineoa"          # project id จริงของ Firebase "BaseLineOA"
GPU_TYPE="nvidia-l4"                       # ปรับตาม service gitbackend-gpu จริง
# --------------------------------------------------

NEW_PROJECT_ID="${1:-}"
REPO_ROOT="$(pwd)"

if [[ -z "$NEW_PROJECT_ID" ]]; then
  echo "Usage: $0 <new_project_id>   เช่น $0 serverwork2"
  exit 1
fi

echo "=== [1/6] สร้าง GCP Project: $NEW_PROJECT_ID ==="
if gcloud projects describe "$NEW_PROJECT_ID" >/dev/null 2>&1; then
  echo "  -> project มีอยู่แล้ว ข้ามขั้นตอนสร้าง"
else
  gcloud projects create "$NEW_PROJECT_ID" --name="$NEW_PROJECT_ID"
  gcloud billing projects link "$NEW_PROJECT_ID" --billing-account="$BILLING_ACCOUNT_ID"
fi

echo "=== [2/6] เปิดใช้งาน API ที่จำเป็น ==="
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  --project="$NEW_PROJECT_ID"

echo "=== [3/6] Deploy services จาก source (เหมือนที่เห็นเป็น 'Repository' บน console) ==="

gcloud run deploy gitbackend \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT" \
  --allow-unauthenticated

gcloud run deploy gitbackend-export \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT/export_service" \
  --allow-unauthenticated

gcloud run deploy tfjs-converter \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT/tfjs_service" \
  --allow-unauthenticated

# NOTE: สมมติว่าโฟลเดอร์ของ gitbackend-gpu ชื่อ gpu_service/
# ถ้าใน repo จริงชื่ออื่น ให้แก้ path ด้านล่างนี้
gcloud run deploy gitbackend-gpu \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT/gpu_service" \
  --allow-unauthenticated \
  --gpu=1 --gpu-type="$GPU_TYPE"

echo "=== [4/6] Deploy Job: train-yolo-job ==="
# NOTE: สมมติโฟลเดอร์ของ job ชื่อ yolo_job/ ปรับ path ตามจริง
gcloud run jobs deploy train-yolo-job \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT/yolo_job"

echo "=== [5/6] ดึง URL จริงของ gitbackend ที่เพิ่ง deploy ==="
CLOUD_URL=$(gcloud run services describe gitbackend \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --format='value(status.url)')
echo "  -> $CLOUD_URL"

echo "=== [6/6] ลงทะเบียน worker ใหม่เข้า Hub Firestore ==="
python3 "$(dirname "$0")/register_worker.py" \
  --hub-project="$HUB_FIREBASE_PROJECT" \
  --worker-id="$NEW_PROJECT_ID" \
  --cloud-url="$CLOUD_URL"

echo ""
echo "✅ เสร็จแล้ว: $NEW_PROJECT_ID พร้อมทำงาน และถูกลงทะเบียนใน hub_system/server_pool/servers/$NEW_PROJECT_ID"
