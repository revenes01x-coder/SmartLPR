"""
label_tool.py
โปรแกรม GUI สำหรับแปะ label สีรถให้รูปที่ crop มาจาก crop_cars_yolo.py

วิธีใช้:
    1. รัน crop_cars_yolo.py ให้เสร็จก่อน (จะได้รูปรถ crop แล้วอยู่ใน CROP_DIR)
    2. รันไฟล์นี้: python label_tool.py
    3. โปรแกรมจะโชว์รูปทีละรูป กดปุ่มสี (หรือกดปุ่มตัวเลข/สัญลักษณ์ตามที่โชว์บนปุ่ม)
       -> ไฟล์รูปนั้นจะถูก "ย้าย" ไปที่ LABELED_DIR/<สีที่เลือก>/ ทันที แล้วโชว์รูปถัดไป
    4. ทำจนครบทุกรูป จากนั้นเอาโฟลเดอร์ใน LABELED_DIR ไป copy รวมกับ dataset หลักได้เลย

ปุ่มลัดคีย์บอร์ด: กดเลข 1-9, 0, - ตามที่เขียนไว้บนปุ่มแต่ละสีได้ (เร็วกว่าคลิกเมาส์)
ปุ่ม "ข้ามรูปนี้" : เก็บรูปไว้ในโฟลเดอร์ CROP_DIR เหมือนเดิม ไม่ย้ายไปไหน ข้ามไปดูรูปถัดไปก่อน
ปุ่ม "ย้อนกลับ (Undo)" : ยกเลิกการเลือกสีของรูปก่อนหน้า ย้ายไฟล์กลับมาที่ CROP_DIR ให้เลือกใหม่
"""

import os
import shutil
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

# ==========================================
# ⚙️ CONFIG — path มาจาก training/train_config.py
# ==========================================
CROP_DIR = str(cfg.COLOR_CROPPED_DIR)   # โฟลเดอร์รูปรถที่ crop มาแล้ว (จาก crop_cars_yolo.py)
LABELED_DIR = str(cfg.COLOR_LABELED_DIR)  # โฟลเดอร์ปลายทาง จะมีโฟลเดอร์ย่อยแยกตามสี

# หมายเหตุ: ชื่อสีชุดนี้ (11 สี มี beige/silver) ยังไม่ตรงกับ class ของโมเดลที่ใช้อยู่ (15 สีจาก VCoR ดู models/color/class_names.json)
# ถ้าจะเอารูปที่ label จากโปรแกรมนี้ไปรวมกับ archive/ ต้องใช้ชื่อโฟลเดอร์สีให้ตรงกันก่อน
COLORS = [
    "beige", "black", "blue", "brown", "green",
    "grey", "pink", "red", "silver", "white", "yellow",
]

# ปุ่มลัดคีย์บอร์ด เรียงตามลำดับสีด้านบน (ต้องมีจำนวนเท่ากับ COLORS)
KEY_SHORTCUTS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-"]

VALID_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
MAX_DISPLAY_SIZE = (500, 500)   # ขนาดสูงสุดที่โชว์รูปในหน้าจอ (ไม่กระทบไฟล์จริง)


class LabelToolApp:
    def __init__(self, root):
        self.root = root
        self.root.title("แปะ label สีรถ")
        self.root.geometry("700x750")

        for color in COLORS:
            os.makedirs(os.path.join(LABELED_DIR, color), exist_ok=True)

        self.queue = self._load_queue()
        self.total_count = len(self.queue)   # จำนวนรูปทั้งหมดตอนเริ่ม ใช้โชว์ความคืบหน้า
        self.labeled_count = 0                # จำนวนรูปที่เลือกสีไปแล้วจริง (ไม่นับข้าม)
        self.history = []  # เก็บ (ไฟล์ต้นทาง, ไฟล์ปลายทาง) ไว้ทำ undo
        self.tk_img = None  # กัน garbage collect รูปที่โชว์อยู่

        self._build_ui()
        self._show_next_image()

    def _load_queue(self):
        if not os.path.isdir(CROP_DIR):
            return []
        files = [f for f in os.listdir(CROP_DIR) if f.lower().endswith(VALID_EXTENSIONS)]
        files.sort()
        return files

    def _build_ui(self):
        self.status_label = ttk.Label(self.root, text="", font=("Segoe UI", 11))
        self.status_label.pack(pady=(10, 5))

        self.image_label = ttk.Label(self.root)
        self.image_label.pack(pady=10)

        button_frame = ttk.Frame(self.root)
        button_frame.pack(pady=10)

        # ปุ่มสีแบบ grid 3 คอลัมน์
        for i, color in enumerate(COLORS):
            key = KEY_SHORTCUTS[i]
            btn_text = f"[{key}] {color}"
            btn = ttk.Button(
                button_frame, text=btn_text, width=16,
                command=lambda c=color: self._assign_color(c),
            )
            btn.grid(row=i // 3, column=i % 3, padx=5, pady=5)
            self.root.bind(key, lambda event, c=color: self._assign_color(c))

        control_frame = ttk.Frame(self.root)
        control_frame.pack(pady=15)

        skip_btn = ttk.Button(control_frame, text="ข้ามรูปนี้ (ยังไม่ลบ)", command=self._skip_image)
        skip_btn.grid(row=0, column=0, padx=10)

        undo_btn = ttk.Button(control_frame, text="ย้อนกลับ (Undo)", command=self._undo_last)
        undo_btn.grid(row=0, column=1, padx=10)

    def _show_next_image(self):
        if not self.queue:
            self.image_label.config(image="", text="✅ เลือกครบทุกรูปแล้ว!")
            self.status_label.config(
                text=f"ทำไปแล้ว {self.labeled_count}/{self.total_count} รูป — "
                     f"เอาโฟลเดอร์สีใน LABELED_DIR ไปรวม dataset ได้เลย"
            )
            return

        self.current_fname = self.queue[0]
        img_path = os.path.join(CROP_DIR, self.current_fname)

        img = Image.open(img_path)
        img.thumbnail(MAX_DISPLAY_SIZE)
        self.tk_img = ImageTk.PhotoImage(img)
        self.image_label.config(image=self.tk_img, text="")

        self.status_label.config(
            text=f"กำลังดู: {self.current_fname}  |  "
                 f"ทำไปแล้ว {self.labeled_count}/{self.total_count} รูป  |  "
                 f"เหลืออีก {len(self.queue)} รูป"
        )

    def _assign_color(self, color):
        if not self.queue:
            return

        fname = self.queue.pop(0)
        src = os.path.join(CROP_DIR, fname)
        dst_dir = os.path.join(LABELED_DIR, color)
        dst = os.path.join(dst_dir, fname)

        # กันไฟล์ชื่อซ้ำในโฟลเดอร์ปลายทาง
        counter = 1
        base_dst = dst
        while os.path.exists(dst):
            name, ext = os.path.splitext(base_dst)
            dst = f"{name}_{counter}{ext}"
            counter += 1

        shutil.move(src, dst)
        self.history.append((src, dst))
        self.labeled_count += 1

        self._show_next_image()

    def _skip_image(self):
        if not self.queue:
            return
        # ย้ายไปต่อท้ายคิว ให้วนกลับมาเจอใหม่ทีหลัง
        fname = self.queue.pop(0)
        self.queue.append(fname)
        self._show_next_image()

    def _undo_last(self):
        if not self.history:
            self.status_label.config(text="ไม่มีประวัติให้ย้อนกลับแล้ว")
            return

        src, dst = self.history.pop()
        # ย้ายไฟล์จากโฟลเดอร์สี กลับไปที่ CROP_DIR (src เดิม)
        shutil.move(dst, src)
        fname = os.path.basename(src)
        self.queue.insert(0, fname)
        self.labeled_count -= 1
        self._show_next_image()


def main():
    root = tk.Tk()
    app = LabelToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()