"""ค่าตั้งของ service camera — อ่านจาก Environment Variable (ไฟล์ camera/.env)

[ทำไมต้องมีไฟล์นี้แยกจาก backend]: เดิม camera ใช้ smartlpr/config.py ร่วมกับ backend ทำให้ camera
ต้องมีค่าที่ไม่ได้ใช้เลย (SECRET_KEY, SMTP_USER, SMTP_APP_PASSWORD ...) ถึงจะเปิดได้ พอแยกเป็นโปรเจคของ
ตัวเองแล้ว camera จึงมี config ของตัวเอง เก็บเฉพาะค่าที่ camera ใช้จริง
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"ไม่พบ Environment Variable '{name}' — กรุณาตั้งค่าใน camera/.env ก่อนรันระบบ"
        )
    return value


# ---- Database (อ่านอย่างเดียว: ดึงรายชื่อกล้องที่ต้องเฝ้า ดู camera/db.py) ----
DATABASE_URL = _require("DATABASE_URL")

# ---- ส่งผลการตรวจจับไปให้ backend ----
CAPTURE_EVENT_WEBHOOK_URL = os.getenv("CAPTURE_EVENT_WEBHOOK_URL", "http://backend:8000/capture-event")
# ต้องเป็นค่าเดียวกับ CAPTURE_EVENT_SECRET ใน backend/.env ไม่งั้น backend จะตอบ 401
CAPTURE_EVENT_SECRET = _require("CAPTURE_EVENT_SECRET")

# ---- รูปภาพ (โฟลเดอร์นี้แชร์กับ backend — ต้อง mount ที่ path เดียวกันทั้งสอง container) ----
CAPTURES_SAVE_DIR = os.getenv("CAPTURES_SAVE_DIR", "captures")

# ---- SSRF Guard: อนุญาตให้กล้องอยู่ใน IP ภายในได้หรือไม่ ----
ALLOW_PRIVATE_IP = os.getenv("ALLOW_PRIVATE_IP", "false").lower() == "true"

# ---- โมเดล AI ----
PLATE_DETECTION_REPO_PATH = os.getenv("PLATE_DETECTION_REPO_PATH")
PLATE_OCR_MODEL_PATH = os.getenv("PLATE_OCR_MODEL_PATH")
PLATE_OCR_CHAR_FILE = os.getenv("PLATE_OCR_CHAR_FILE")
PLATE_YOLO_MODEL_PATH = os.getenv("PLATE_YOLO_MODEL_PATH")
# ไฟล์โมเดลจริงอยู่ที่ models/detector/yolo11n.pt — ถ้าใส่แค่ "yolo11n.pt" ultralytics จะแอบดาวน์โหลดใหม่
# จากอินเทอร์เน็ตทุกครั้งที่ container เริ่ม (เครื่องไม่มีเน็ต = AI worker crash วน)
CAR_DETECTOR_MODEL_PATH = os.getenv("CAR_DETECTOR_MODEL_PATH", "models/detector/yolo11n.pt")
CAR_COLOR_MODEL_PATH = os.getenv("CAR_COLOR_MODEL_PATH")
CAR_COLOR_CLASSNAMES_PATH = os.getenv("CAR_COLOR_CLASSNAMES_PATH")

# ---- จำนวนเฟรมต่อวินาทีที่ส่งเข้า AI ----
CAMERA_DETECTION_FPS = float(os.getenv("CAMERA_DETECTION_FPS", "5.0"))
