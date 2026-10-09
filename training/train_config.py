"""
train_config.py — รวม path ทั้งหมดที่สคริปต์ train / สร้าง dataset ใช้ ไว้ที่เดียว

ทำไมต้องมีไฟล์นี้?
    เดิมแต่ละสคริปต์เขียน path แบบ C:\\Users\\ACER\\... ไว้ในตัวเองคนละที่
    พอย้ายโฟลเดอร์ หรือย้าย dataset ไปไว้ที่อื่น ต้องไล่แก้ทีละไฟล์ (และบางไฟล์ก็ชี้ไปที่ที่ไม่มีแล้ว)
    ตอนนี้ทุกสคริปต์มาอ่าน path จากไฟล์นี้ แก้ที่นี่ที่เดียวพอ

แบ่ง path เป็น 2 ฝั่ง:
    1) PROJECT_DIR = โปรเจกต์ SmartLPR (โค้ด + โมเดลที่ระบบใช้จริง)
    2) DATA_ROOT   = ที่เก็บ dataset (รูป, label, lmdb) — dataset ไม่ได้ย้ายมาใน SmartLPR
                     เพราะไฟล์ใหญ่มาก ยังอยู่ที่ PlateDetection เหมือนเดิม
                     ถ้าย้าย dataset ไปที่อื่น: แก้ DEFAULT_DATA_ROOT ข้างล่าง
                     หรือตั้ง environment variable SMARTLPR_DATA_ROOT ก็ได้
"""
import os
from pathlib import Path

# ---------- ตำแหน่งโปรเจกต์ (คำนวณจากตำแหน่งไฟล์นี้เอง ย้ายโปรเจกต์ไปไหนก็ยังถูก) ----------
TRAINING_DIR = Path(__file__).resolve().parent   # .../SmartLPR/training
PROJECT_DIR  = TRAINING_DIR.parent               # .../SmartLPR

# ---------- ที่เก็บ dataset ----------
DEFAULT_DATA_ROOT = r"C:\Users\ACER\Downloads\MyProject\PlateDetection"
DATA_ROOT = Path(os.environ.get("SMARTLPR_DATA_ROOT", DEFAULT_DATA_ROOT))

# ---------- ผลลัพธ์จากการ train (โมเดลใหม่, log, กราฟ) ----------
# แยกไว้ใน training/outputs จะได้ไม่ไปทับโมเดลที่ระบบใช้งานจริงใน models/ โดยไม่ตั้งใจ
OUTPUT_DIR = TRAINING_DIR / "outputs"

# ---------- โมเดล / ไฟล์ที่ระบบหลักใช้งานจริง ----------
PROD_MODELS_DIR  = PROJECT_DIR / "models"
PLATE_YOLO_MODEL = PROD_MODELS_DIR / "yolo" / "best.pt"           # YOLO หาป้ายทะเบียน
CAR_YOLO_MODEL   = PROD_MODELS_DIR / "detector" / "yolo11n.pt"    # YOLO หารถ (COCO pretrained)
COLOR_MODEL_PROD = PROD_MODELS_DIR / "color" / "car_color_model.h5"
CHAR_FILE        = PROJECT_DIR / "thai_plate_chars.txt"            # ชุดตัวอักษรของ OCR (ต้องตรงกับตอนใช้งานจริง)

# =====================================================================
# 1) YOLO ตรวจจับป้ายทะเบียน (dataset export มาจาก Roboflow)
# =====================================================================
YOLO_DATA_YAML  = DATA_ROOT / "thai_plate_detection.v3i.yolov11" / "data.yaml"
YOLO_OUTPUT_DIR = OUTPUT_DIR / "yolo"

# =====================================================================
# 2) OCR อ่านป้ายทะเบียน (deep-text-recognition-benchmark)
# =====================================================================
# --- สร้าง dataset ---
OCR_CROP_DIR         = DATA_ROOT / "Cropped_Plates"     # รูปป้ายที่ crop แล้ว
OCR_DELETED_DIR      = DATA_ROOT / "Deleted_Plates"     # รูปที่กดลบตอนทำ label
OCR_LABELS_CSV       = DATA_ROOT / "labels.csv"         # เฉลยที่พิมพ์จากโปรแกรม label
OCR_LABELS_CLEAN_CSV = DATA_ROOT / "labels_clean.csv"   # เฉพาะแถวที่ทำเสร็จแล้ว
OCR_TRAIN_LIST       = DATA_ROOT / "train_list.txt"     # รูปแบบ "ชื่อไฟล์ เลขทะเบียน จังหวัด"
OCR_SPLIT_DIR        = DATA_ROOT / "dataset_split"      # แบ่ง train/val/test แล้ว
OCR_LMDB_DIR         = DATA_ROOT / "data_lmdb"          # lmdb เลขทะเบียนอย่างเดียว
OCR_LMDB_FULL_DIR    = DATA_ROOT / "data_lmdb_full"     # lmdb เลขทะเบียน + จังหวัด

# --- train ---
DTRB_DIR        = TRAINING_DIR / "ocr" / "deep_text_recognition"   # โค้ด train ของ deep-text-recognition-benchmark
OCR_OUTPUT_DIR  = OUTPUT_DIR / "ocr"     # train.py จะสร้าง saved_models/ และ result/ ไว้ในนี้
OCR_PRETRAINED  = DATA_ROOT / "deep-text-recognition-benchmark" / "thai.pth"   # โมเดลตั้งต้นภาษาไทย
OCR_LEGACY_SAVED = DATA_ROOT / "deep-text-recognition-benchmark" / "saved_models"  # ผล train เก่าที่ PlateDetection
OCR_EXP_NAME      = "thai_plate_ocr"        # ขั้นที่ 1: เลขทะเบียนอย่างเดียว
OCR_EXP_NAME_FULL = "thai_plate_ocr_full"   # ขั้นที่ 2: เลขทะเบียน + จังหวัด

# =====================================================================
# 3) โมเดลแยกสีรถ (EfficientNetB0)
# =====================================================================
COLOR_DATASET_DIR = DATA_ROOT / "archive"                     # VCoR dataset (train/val/test/<สี>)
COLOR_RAW_DIR     = DATA_ROOT / "images" / "crop_car"         # รูปดิบจากกล้อง
COLOR_CROPPED_DIR = DATA_ROOT / "images" / "cropped_cars"     # รูปรถที่ crop แล้ว รอแปะ label
COLOR_LABELED_DIR = DATA_ROOT / "images" / "labeled_cars"     # รูปที่แปะ label สีแล้ว แยกโฟลเดอร์ตามสี
COLOR_OUTPUT_DIR  = OUTPUT_DIR / "color"


def first_existing(*paths):
    """คืน path แรกที่มีไฟล์อยู่จริง (ถ้าไม่มีเลย คืนตัวแรก เพื่อให้ error บอกชื่อไฟล์ที่คาดไว้)"""
    for p in paths:
        if Path(p).exists():
            return Path(p)
    return Path(paths[0])
