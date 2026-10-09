# recreate_train_list.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg
INPUT_CSV  = cfg.OCR_LABELS_CLEAN_CSV
OUTPUT_TXT = cfg.OCR_TRAIN_LIST

with open(INPUT_CSV, encoding="utf-8-sig") as f_in, \
     open(OUTPUT_TXT, "w", encoding="utf-8") as f_out:
    for line in f_in:
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("filename,"):
            continue  # ข้ามบรรทัดหัวตาราง CSV (เดิมหลุดเข้าไปใน train_list.txt เป็น "filename text done")
        parts = line.split(",")
        if len(parts) < 2:
            continue

        filename = parts[0]          # crop_xxx.png
        label    = parts[1]          # เลขทะเบียน
        province = parts[2] if len(parts) > 2 and parts[2] not in ("YES", "NO", "") else ""

        if province:
            f_out.write(f"{filename} {label} {province}\n")
        else:
            f_out.write(f"{filename} {label}\n")

print(" สร้าง train_list.txt เสร็จแล้ว")