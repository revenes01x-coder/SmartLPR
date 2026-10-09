import cv2 as cv
from ultralytics import YOLO
import datetime
import os
import glob
import tkinter as tk
from tkinter import filedialog
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

print("........กำลังโหลดระบบ Fast Batch Crop........")

# ================= 1. ตั้งค่า Model (ตั้งแค่ครั้งเดียว) =================
YOLO_MODEL_PATH = str(cfg.PLATE_YOLO_MODEL)  # ใช้โมเดลหาป้ายตัวเดียวกับที่ระบบ SmartLPR ใช้จริง (models/yolo/best.pt)
yolo_model = YOLO(YOLO_MODEL_PATH)

YOLO_CONF = 0.1
MIN_ASPECT_RATIO = 0.8  
MIN_WIDTH = 50
# ลบ PADDING = 10 แบบเก่าทิ้งไป เพราะเราใช้ระบบเปอร์เซ็นต์แทนแล้ว
# =================================================================

# ================= 2. สร้างหน้าต่างเลือกโฟลเดอร์ =================
# ซ่อนหน้าต่างหลักของ tkinter ไม่ให้เกะกะ
root = tk.Tk()
root.withdraw() 
root.attributes('-topmost', True) # บังคับให้หน้าต่างเด้งอยู่หน้าสุด

print("\nรอสักครู่... กรุณาเลือก 'โฟลเดอร์' ที่เก็บรูปรถจากหน้าต่างที่เด้งขึ้นมาครับ")
input_folder = filedialog.askdirectory(title="เลือกโฟลเดอร์ที่เก็บรูปรถทั้งหมด")

# ถ้าผู้ใช้กดกากบาทปิดหน้าต่าง หรือไม่ได้เลือกโฟลเดอร์ ให้จบโปรแกรม
if not input_folder:
    print("ยกเลิกการทำงาน: ไม่ได้เลือกโฟลเดอร์ครับ")
    exit()

print(f"\n โฟลเดอร์ที่เลือก: {input_folder}")

# ================= 3. เตรียมโฟลเดอร์สำหรับ Save =================
# สร้างโฟลเดอร์ชื่อ "Cropped_Plates" ไว้ข้างในโฟลเดอร์เดิม เพื่อความแฮปปี้และเป็นระเบียบ
output_folder = os.path.join(input_folder, "Cropped_Plates")
os.makedirs(output_folder, exist_ok=True) # ถ้ามีโฟลเดอร์นี้อยู่แล้วก็ไม่เป็นไร ผ่านได้เลย

# ================= 4. สแกนหาไฟล์รูปภาพทั้งหมด =================
# ค้นหาไฟล์ .jpg, .jpeg, .png ในโฟลเดอร์นั้น
image_files = []
for ext in ('*.jpg', '*.jpeg', '*.png'):
    image_files.extend(glob.glob(os.path.join(input_folder, ext)))

total_images = len(image_files)
if total_images == 0:
    print("❌ ไม่พบไฟล์รูปภาพในโฟลเดอร์นี้เลยครับ")
    exit()

print(f" พบรูปภาพทั้งหมด: {total_images} รูป... เริ่มลุยกันเลย!\n")

# ================= 5. เริ่มกระบวนการ Crop อัตโนมัติ (ลูปทีละรูป) =================
plate_counter = 1
timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

for i, img_path in enumerate(image_files, start=1):
    img = cv.imread(img_path)
    if img is None:
        print(f"⚠️ อ่านไฟล์ไม่ได้ ข้าม: {os.path.basename(img_path)}")
        continue

    # สั่ง YOLO หาป้ายในรูปนี้
    results = yolo_model(img, verbose=False)

    for result in results:
        for box in result.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            yolo_conf = box.conf[0].item()
            
            width = x2 - x1
            height = y2 - y1
            if height > 0:
                aspect_ratio = width / height  
            else:
                aspect_ratio = 0
            
            if aspect_ratio < MIN_ASPECT_RATIO or width < MIN_WIDTH:
                continue

            if yolo_conf > YOLO_CONF:
                # อัปเกรด 1: คำนวณระยะเผื่อขอบแบบยืดหยุ่น (Dynamic Padding)
                pad_x = int(width * 0.05)   # ขยายซ้าย-ขวา 5%
                pad_y = int(height * 0.15)  # ขยายบน-ล่าง 15%

                # คำนวณพิกัดใหม่
                y1_p = max(0, y1 - pad_y)
                y2_p = min(img.shape[0], y2 + pad_y)
                x1_p = max(0, x1 - pad_x)
                x2_p = min(img.shape[1], x2 + pad_x)
                
                # ตัดรูปดิบ
                plate_crop = img[y1_p:y2_p, x1_p:x2_p]

                # อัปเกรด 2: ขยายขนาดรูปขึ้น 2.5 เท่า เพื่อความคมชัด (Cubic Interpolation)
                plate_crop_resized = cv.resize(plate_crop, None, fx=2.5, fy=2.5, interpolation=cv.INTER_CUBIC)

                #  อัปเกรด 3: เซฟไฟล์เป็นนามสกุล .png
                filename = f"crop_{timestamp}_{plate_counter}.png"
                save_path = os.path.join(output_folder, filename)
                
                # บันทึกรูปที่ขยายและชัดแล้ว
                cv.imwrite(save_path, plate_crop_resized)
                
                plate_counter += 1

    # ปริ้นท์บอกความคืบหน้า เพื่อให้เรารู้ว่าโปรแกรมไม่ค้าง
    print(f"[{i}/{total_images}] ประมวลผลรูป {os.path.basename(img_path)} เสร็จสิ้น")

print(f"\n เสร็จเรียบร้อย! ตัดป้ายทะเบียนได้ทั้งหมด {plate_counter - 1} ป้าย")
print(f" เข้าไปดูผลลัพธ์ได้ที่: {output_folder}")