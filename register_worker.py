"""
register_worker.py

ลงทะเบียน (หรืออัปเดต) worker document ใน Hub Firestore:
  hub_system/server_pool/servers/{worker_id}

ใช้ Application Default Credentials — ต้องรัน `gcloud auth application-default login`
มาก่อน 1 ครั้ง หรือรันบนเครื่องที่มี service account key/permission เข้าถึง
Firestore ของ project Hub (BaseLineOA) อยู่แล้ว

ตัวอย่างการเรียกใช้เดี่ยว ๆ (ไม่ผ่าน deploy script):
    python3 register_worker.py \
        --hub-project=baselineoa \
        --worker-id=serverwork2 \
        --cloud-url=https://gitbackend-xxxxx.asia-southeast1.run.app
"""

import argparse
import time

import firebase_admin
from firebase_admin import credentials, firestore


def register_worker(hub_project: str, worker_id: str, cloud_url: str) -> None:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {"projectId": hub_project})
    db = firestore.client()

    doc_ref = (
        db.collection("hub_system")
        .document("server_pool")
        .collection("servers")
        .document(worker_id)
    )

    doc_ref.set(
        {
            "server_id": worker_id,
            "cloud_url": cloud_url,
            "status": "online",
            "active_users": 0,
            "load_score": 0,
            "last_heartbeat": int(time.time()),
        },
        merge=True,
    )

    print(f"Registered '{worker_id}' -> {cloud_url}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub-project", required=True, help="Firebase project id ของ Hub เช่น baselineoa")
    parser.add_argument("--worker-id", required=True, help="ชื่อ project/worker เช่น serverwork2")
    parser.add_argument("--cloud-url", required=True, help="URL ของ Cloud Run service gitbackend ที่เพิ่ง deploy")
    args = parser.parse_args()

    register_worker(args.hub_project, args.worker_id, args.cloud_url)


if __name__ == "__main__":
    main()
