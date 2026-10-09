import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg
# เขียนลง training/outputs/ ไม่เขียนทับ thai_plate_chars.txt ของระบบจริง
# (ไฟล์ของระบบจริงมีสระ/วรรณยุกต์ด้วย และต้องตรงกับโมเดล OCR ที่ใช้อยู่ ถ้าทับแล้วโมเดลจะอ่านเพี้ยน)
cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
output_path = cfg.OUTPUT_DIR / "thai_plate_chars.txt"
thai = "กขคฆงจฉชฌญฎฐฒณดตถทธนบปผพฟภมยรลวศษสหฬอฮ "
digits = "0123456789"
all_chars = sorted(set(thai + digits))

with open(output_path, "w", encoding="utf-8") as f:
    f.write("".join(all_chars))

print(f" สร้างไฟล์ {output_path} สำเร็จ")
print(f"   จำนวนตัวอักษรทั้งหมด: {len(all_chars)} ตัว")
print(f"   ไทย: {len(thai)} | เลข: {len(digits)}")
print(f"\nตัวอักษรทั้งหมด:")
print("".join(all_chars))