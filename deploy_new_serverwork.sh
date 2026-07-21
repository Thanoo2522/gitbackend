#!/usr/bin/env bash
# =====================================================================
# deploy_new_serverwork.sh
#
# สร้าง Cloud Run project ใหม่ (serverwork2, serverwork3, ...) แบบครบชุด
# แล้วลงทะเบียนเข้า Hub Firestore (project baselineoa) อัตโนมัติ
#
# วิธีใช้:
#   ./deploy_new_serverwork.sh <new_project_id> <worker_id>
#   เช่น     ./deploy_new_serverwork.sh serverwork2 worker2
#
# ก่อนรันครั้งแรก ต้องมีไฟล์ 2 อันนี้อยู่ในโฟลเดอร์เดียวกับสคริปต์:
#   worker-sa.json   -> service account key ของ project basework-51f3b
#                       (ใช้ตอนนี้ค่าเดียวกันทุก worker ตามที่ระบุ)
#   hub-sa.json      -> service account key ของ project baselineoa (Hub)
#
#   *** ถ้ายังไม่ได้ rotate key ที่เคยแปะในแชท ให้ไป generate key ใหม่
#       ใน Firebase Console ก่อน แล้วเซฟทับไฟล์ 2 อันนี้ ***
#
#   chmod +x deploy_new_serverwork.sh
#   gcloud auth login
#   pip install firebase-admin --break-system-packages
# =====================================================================
set -euo pipefail

# ---------- CONFIG: แก้ให้ตรงกับของจริง ----------
REGION="asia-southeast1"                   # region เดียวสำหรับทุก service (ง่ายกว่าผสม region แบบ serverwork1)
BILLING_ACCOUNT_ID="012EF4-63F4C9-F71E94"   # billing account เดียวกับ serverworker1
HUB_FIREBASE_PROJECT="baselineoa"
GPU_TYPE="nvidia-l4"                        # ยืนยันแล้วจากของจริง
GPU_CPU="4"                                 # 4000m ตามของจริง
GPU_MEMORY="16Gi"
ADMIN_SECRET_KEY="CHANGE_ME_OR_LOAD_FROM_SECRET_MANAGER"  # << อย่าฝัง secret จริงในไฟล์นี้ตรง ๆ ถ้าจะ push เข้า git
# --------------------------------------------------

NEW_PROJECT_ID="${1:-}"
WORKER_ID="${2:-}"
REPO_ROOT="$(pwd)"
SCRIPT_DIR="$(dirname "$0")"

WORKER_SA_FILE="$SCRIPT_DIR/worker-sa.json"
HUB_SA_FILE="$SCRIPT_DIR/hub-sa.json"

if [[ -z "$NEW_PROJECT_ID" || -z "$WORKER_ID" ]]; then
  echo "Usage: $0 <new_project_id> <worker_id>   เช่น $0 serverwork2 worker2"
  exit 1
fi

if [[ ! -f "$WORKER_SA_FILE" || ! -f "$HUB_SA_FILE" ]]; then
  echo "ไม่พบ $WORKER_SA_FILE หรือ $HUB_SA_FILE — วางไฟล์ key ให้ครบก่อนรัน (ห้าม commit ไฟล์นี้เข้า git)"
  exit 1
fi

WORKER_FIREBASE_KEY_JSON="$(cat "$WORKER_SA_FILE")"
HUB_FIREBASE_KEY_JSON="$(cat "$HUB_SA_FILE")"

echo "=== [1/8] สร้าง GCP Project: $NEW_PROJECT_ID ==="
if gcloud projects describe "$NEW_PROJECT_ID" >/dev/null 2>&1; then
  echo "  -> project มีอยู่แล้ว ข้ามขั้นตอนสร้าง"
else
  gcloud projects create "$NEW_PROJECT_ID" --name="$NEW_PROJECT_ID"
fi

echo "  -> ผูก billing account (รันซ้ำได้ ไม่มีผลเสียถ้าผูกอยู่แล้ว)"
gcloud billing projects link "$NEW_PROJECT_ID" --billing-account="$BILLING_ACCOUNT_ID"

echo "=== [2/8] เปิดใช้งาน API ที่จำเป็น ==="
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  --project="$NEW_PROJECT_ID"

# ---------------------------------------------------------------------
# ลำดับ deploy สำคัญมาก: gitbackend และ gitbackend-gpu ต้องรู้ URL ของ
# tfjs-converter กับ gitbackend-export ล่วงหน้า (อ้างผ่าน env var)
# จึงต้อง deploy 2 ตัวนี้ก่อน
# ---------------------------------------------------------------------

echo "=== [3/8] Deploy tfjs-converter ==="
gcloud run deploy tfjs-converter \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT/tfjs_service" \
  --allow-unauthenticated
TFJS_URL=$(gcloud run services describe tfjs-converter \
  --project="$NEW_PROJECT_ID" --region="$REGION" --format='value(status.url)')
echo "  -> $TFJS_URL"

echo "=== [4/8] Deploy gitbackend-export ==="
gcloud run deploy gitbackend-export \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT/export_service" \
  --allow-unauthenticated
EXPORT_URL=$(gcloud run services describe gitbackend-export \
  --project="$NEW_PROJECT_ID" --region="$REGION" --format='value(status.url)')
echo "  -> $EXPORT_URL"

echo "=== [5/8] Deploy gitbackend (พร้อม env vars ครบ) ==="
cat > /tmp/env-gitbackend.yaml <<EOF
WORKER_FIREBASE_KEY: |-
$(echo "$WORKER_FIREBASE_KEY_JSON" | sed 's/^/  /')
HUB_FIREBASE_KEY: |-
$(echo "$HUB_FIREBASE_KEY_JSON" | sed 's/^/  /')
SERVER_ID: "$WORKER_ID"
ADMIN_SECRET_KEY: "$ADMIN_SECRET_KEY"
TFJS_SERVICE_URL: "$TFJS_URL"
EXPORT_SERVICE_URL: "$EXPORT_URL"
EOF

gcloud run deploy gitbackend \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT" \
  --allow-unauthenticated \
  --env-vars-file=/tmp/env-gitbackend.yaml
GITBACKEND_URL=$(gcloud run services describe gitbackend \
  --project="$NEW_PROJECT_ID" --region="$REGION" --format='value(status.url)')
echo "  -> $GITBACKEND_URL"

echo "=== [6/8] Deploy gitbackend-gpu (source เดียวกับ gitbackend + GPU + env vars) ==="
cat > /tmp/env-gitbackend-gpu.yaml <<EOF
WORKER_FIREBASE_KEY: |-
$(echo "$WORKER_FIREBASE_KEY_JSON" | sed 's/^/  /')
HUB_FIREBASE_KEY: |-
$(echo "$HUB_FIREBASE_KEY_JSON" | sed 's/^/  /')
SERVER_ID: "$WORKER_ID"
ADMIN_SECRET_KEY: "$ADMIN_SECRET_KEY"
WORKER_WEBHOOK_URL: "$GITBACKEND_URL"
TFJS_SERVICE_URL: "$TFJS_URL"
EXPORT_SERVICE_URL: "$EXPORT_URL"
EOF

gcloud run deploy gitbackend-gpu \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --source="$REPO_ROOT" \
  --allow-unauthenticated \
  --cpu="$GPU_CPU" --memory="$GPU_MEMORY" \
  --gpu=1 --gpu-type="$GPU_TYPE" \
  --no-gpu-zonal-redundancy \
  --no-cpu-throttling \
  --env-vars-file=/tmp/env-gitbackend-gpu.yaml
GPU_URL=$(gcloud run services describe gitbackend-gpu \
  --project="$NEW_PROJECT_ID" --region="$REGION" --format='value(status.url)')
echo "  -> $GPU_URL"

# ---------------------------------------------------------------------
# train-yolo-job: ใช้ image เดียวกับ gitbackend เป๊ะ ๆ (เช็คจาก YAML จริง
# แล้ว ไม่มี source แยก ไม่มี env vars พิเศษ) เลยดึง image ที่เพิ่ง
# deploy ให้ gitbackend มาใช้ซ้ำ แทนที่จะ build ใหม่
# ---------------------------------------------------------------------
echo "=== [7/8] Deploy train-yolo-job (ใช้ image เดียวกับ gitbackend) ==="
GITBACKEND_IMAGE=$(gcloud run services describe gitbackend \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --format='value(spec.template.spec.containers[0].image)')

gcloud run jobs deploy train-yolo-job \
  --project="$NEW_PROJECT_ID" --region="$REGION" \
  --image="$GITBACKEND_IMAGE" \
  --cpu=1 --memory=512Mi \
  --max-retries=3 --task-timeout=600

echo "=== [8/8] ลงทะเบียน gitbackend (entry point หลัก) เข้า Hub Firestore ==="
python3 "$SCRIPT_DIR/register_worker.py" \
  --hub-project="$HUB_FIREBASE_PROJECT" \
  --worker-id="$WORKER_ID" \
  --cloud-url="$GITBACKEND_URL" \
  --key-file="$HUB_SA_FILE"

rm -f /tmp/env-gitbackend.yaml /tmp/env-gitbackend-gpu.yaml

echo ""
echo "✅ เสร็จแล้ว: $NEW_PROJECT_ID / $WORKER_ID"
echo "   gitbackend:        $GITBACKEND_URL"
echo "   gitbackend-export: $EXPORT_URL"
echo "   gitbackend-gpu:    $GPU_URL"
echo "   tfjs-converter:    $TFJS_URL"
