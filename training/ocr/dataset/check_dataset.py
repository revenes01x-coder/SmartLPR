# check_dataset.py
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

TRAIN_DIR = cfg.OCR_SPLIT_DIR / "train"
CHARSET   = cfg.CHAR_FILE.read_text(encoding="utf-8").rstrip("\r\n")  # อ่านจากไฟล์เดียวกับที่ใช้ train/ใช้งานจริง

gt_path = os.path.join(TRAIN_DIR, 'labels_full.txt')
labels, lengths, bad_chars = [], [], []

with open(gt_path, encoding='utf-8-sig') as f:
    for line in f:
        parts = line.strip().split('\t')
        if len(parts) != 2:
            continue
        fname, label = parts
        lengths.append(len(label))
        
        # เช็คตัวอักษรที่ไม่อยู่ใน charset
        unknown = [c for c in label if c not in CHARSET]
        if unknown:
            bad_chars.append((fname, label, unknown))

all_chars = set()
with open(gt_path, encoding='utf-8-sig') as f:
    for line in f:
        parts = line.strip().split('\t')
        if len(parts) != 2:
            continue
        _, label = parts
        all_chars.update(label)

print(f"\nตัวอักษรทั้งหมดที่มีใน dataset ({len(all_chars)} ตัว):")
print("".join(sorted(all_chars)))

# แยกประเภท
digits   = sorted([c for c in all_chars if c.isdigit()])
thai     = sorted([c for c in all_chars if '\u0E00' <= c <= '\u0E7F'])
others   = sorted([c for c in all_chars if not c.isdigit() and not '\u0E00' <= c <= '\u0E7F'])

print(f"\nเลข ({len(digits)} ตัว): {''.join(digits)}")
print(f"ไทย ({len(thai)} ตัว):  {''.join(thai)}")
if others:
    print(f"อื่นๆ ({len(others)} ตัว): {''.join(others)}")

print(f"จำนวน labels: {len(lengths)}")
print(f"ความยาว label: min={min(lengths)}, max={max(lengths)}, avg={sum(lengths)/len(lengths):.1f}")
print(f"\nตัวอย่าง label 5 รายการแรก:")
with open(gt_path, encoding='utf-8-sig') as f:
    for i, line in enumerate(f):
        if i >= 5: break
        print(f"  {line.strip()}")

print(f"\nLabels ที่มีตัวอักษรนอก charset: {len(bad_chars)} รายการ")
for fname, label, unk in bad_chars[:5]:
    print(f"  {fname} | '{label}' | ไม่รู้จัก: {unk}")