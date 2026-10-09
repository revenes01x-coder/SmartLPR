"""
crop_cars_yolo.py
อ่านรูปทั้งหมดจากโฟลเดอร์ที่เก็บรูปดิบจากกล้อง (INPUT_DIR)
ใช้ YOLO ตรวจจับรถแต่ละคันในรูป แล้ว crop เฉพาะส่วนรถ เซฟแยกเป็นไฟล์ใหม่ลงในโฟลเดอร์ปลายทาง (OUTPUT_DIR)

รูปที่ได้จากไฟล์นี้ เอาไปใช้ต่อกับ label_tool.py เพื่อเลือกสีทีละรูปได้เลย

วิธีใช้:
    python crop_cars_yolo.py
"""

import os
import cv2 as cv
from ultralytics import YOLO
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

# ==========================================
# ⚙️ CONFIG — path มาจาก training/train_config.py
# ==========================================
INPUT_DIR = str(cfg.COLOR_RAW_DIR)   # โฟลเดอร์ที่เก็บรูปดิบจากกล้อง
OUTPUT_DIR = str(cfg.COLOR_CROPPED_DIR)        # โฟลเดอร์ปลายทางที่จะเก็บรูปรถที่ crop แล้ว

CAR_CLASS_IDS = [2, 3, 5, 7]     # COCO class id: 2=car, 3=motorcycle, 5=bus, 7=truck
YOLO_CONF = 0.4                  # confidence ขั้นต่ำที่ยอมรับว่าเป็นรถจริง (ตรงกับ pipeline หลัก)
CAR_BOX_EXPAND_RATIO = 0.05      # ขยายกรอบออกเล็กน้อย กันตัดขอบรถขาด (ตรงกับ pipeline หลัก)
MIN_WIDTH = 60                   # ไม่เก็บ crop ที่เล็กเกินไป (ไกลกล้องมาก มองสีไม่ออก)

VALID_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")


def expand_and_clip_box(x1, y1, x2, y2, img_w, img_h, expand_ratio):
    """ขยายกรอบออกตามสัดส่วน แล้ว clip ไม่ให้เกินขอบภาพ (เหมือน pipeline หลัก)"""
    w = x2 - x1
    h = y2 - y1
    pad_x = int(w * expand_ratio)
    pad_y = int(h * expand_ratio)
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(img_w, x2 + pad_x)
    y2 = min(img_h, y2 + pad_y)
    return x1, y1, x2, y2


def main():
    print("=" * 60)
    print("🚗 เริ่ม crop รถจากรูปดิบด้วย YOLO")
    print("=" * 60)

    if not os.path.isdir(INPUT_DIR):
        print(f"❌ ไม่เจอโฟลเดอร์ INPUT_DIR: {INPUT_DIR}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\nกำลังโหลดโมเดล YOLO (ตรวจจับรถทั้งคัน)...")
    # ใช้ไฟล์ yolo11n.pt ใน models/detector ของ SmartLPR ถ้ามี (ไม่ต้องโหลดใหม่) ไม่มีค่อยให้ ultralytics โหลดเอง
    car_detector = YOLO(str(cfg.CAR_YOLO_MODEL) if cfg.CAR_YOLO_MODEL.exists() else "yolo11n.pt")

    image_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(VALID_EXTENSIONS)]
    print(f"-> เจอรูปทั้งหมด {len(image_files)} ไฟล์ใน {INPUT_DIR}")

    total_cars_saved = 0
    total_images_with_no_car = 0

    for idx, fname in enumerate(image_files, start=1):
        img_path = os.path.join(INPUT_DIR, fname)
        img = cv.imread(img_path)

        if img is None:
            print(f"[{idx}/{len(image_files)}] ⚠️ อ่านรูปไม่ได้ ข้าม: {fname}")
            continue

        img_h, img_w = img.shape[:2]
        results = car_detector(img, verbose=False)

        cars_found_in_this_image = 0
        stem = os.path.splitext(fname)[0]

        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])

            if cls_id not in CAR_CLASS_IDS or conf <= YOLO_CONF:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0])
            x1, y1, x2, y2 = expand_and_clip_box(x1, y1, x2, y2, img_w, img_h, CAR_BOX_EXPAND_RATIO)

            width = x2 - x1
            if width < MIN_WIDTH:
                continue

            car_crop = img[y1:y2, x1:x2]
            cars_found_in_this_image += 1

            out_name = f"{stem}_car{cars_found_in_this_image}.jpg"
            out_path = os.path.join(OUTPUT_DIR, out_name)

            # กันไฟล์ชื่อซ้ำทับกัน (เช่น รันสคริปต์นี้ซ้ำกับรูปเดิม)
            counter = 1
            while os.path.exists(out_path):
                out_name = f"{stem}_car{cars_found_in_this_image}_{counter}.jpg"
                out_path = os.path.join(OUTPUT_DIR, out_name)
                counter += 1

            cv.imwrite(out_path, car_crop)
            total_cars_saved += 1

        if cars_found_in_this_image == 0:
            total_images_with_no_car += 1

        print(f"[{idx}/{len(image_files)}] {fname} -> เจอรถ {cars_found_in_this_image} คัน")

    print("\n" + "=" * 60)
    print(f"✅ เสร็จแล้ว! crop รถได้ทั้งหมด {total_cars_saved} คัน")
    print(f"   (รูปที่ไม่เจอรถเลย: {total_images_with_no_car} รูป)")
    print(f"   เก็บไว้ที่: {OUTPUT_DIR}")
    print("=" * 60)
    print("\nขั้นต่อไป: รัน label_tool.py เพื่อเลือกสีทีละรูปแล้วแยกลงโฟลเดอร์สีอัตโนมัติ")


if __name__ == "__main__":
    main()