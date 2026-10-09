import lmdb
import cv2
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

def create_lmdb(img_dir, gt_file, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    with open(gt_file, encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip()]

    map_size = 1024 * 1024 * 1024 * 2  # 2GB fixed
    env = lmdb.open(output_dir, map_size=map_size)
    count = 0
    skipped = 0

    with env.begin(write=True) as txn:
        for line in lines:
            parts = line.split()          # ← แก้ตรงนี้ รองรับทั้ง space และ tab
            if len(parts) < 2:
                continue
            filename = parts[0].strip()
            label = " ".join(parts[1:]).strip()
            img_path = os.path.join(img_dir, filename)

            img = cv2.imread(img_path)
            if img is None:
                skipped += 1
                continue

            _, buf = cv2.imencode('.png', img)
            count += 1
            txn.put(f'image-{count:09d}'.encode(), buf.tobytes())
            txn.put(f'label-{count:09d}'.encode(), label.encode('utf-8'))

        txn.put(b'num-samples', str(count).encode())

    print(f" {output_dir}: {count} รูป (ข้าม {skipped} รูป)")

BASE = cfg.OCR_SPLIT_DIR
OUT  = cfg.OCR_LMDB_FULL_DIR

for split in ("train", "val", "test"):
    create_lmdb(str(BASE / split), str(BASE / split / "labels_full.txt"), str(OUT / split))