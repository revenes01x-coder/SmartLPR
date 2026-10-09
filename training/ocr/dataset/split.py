import shutil
import random
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

# ============================================================
#  CONFIG — path มาจาก training/train_config.py
# ============================================================
ANNOTATION_FILE = cfg.OCR_TRAIN_LIST
IMAGE_DIR       = cfg.OCR_CROP_DIR
OUTPUT_DIR      = cfg.OCR_SPLIT_DIR

TRAIN_RATIO = 0.85
VAL_RATIO   = 0.10
TEST_RATIO  = 0.05

RANDOM_SEED = 42
# ============================================================

THAI_PROVINCES = [
    "กรุงเทพมหานคร", "กระบี่", "กาญจนบุรี", "กาฬสินธุ์", "กำแพงเพชร", "ขอนแก่น", "จันทบุรี",
    "ฉะเชิงเทรา", "ชลบุรี", "ชัยนาท", "ชัยภูมิ", "ชุมพร", "เชียงราย", "เชียงใหม่", "ตรัง",
    "ตราด", "ตาก", "นครนายก", "นครปฐม", "นครพนม", "นครราชสีมา", "นครศรีธรรมราช",
    "นครสวรรค์", "นนทบุรี", "นราธิวาส", "น่าน", "บึงกาฬ", "บุรีรัมย์", "เบตง", "ปทุมธานี",
    "ประจวบคีรีขันธ์", "ปราจีนบุรี", "ปัตตานี", "พระนครศรีอยุธยา", "พะเยา", "พังงา",
    "พัทลุง", "พิจิตร", "พิษณุโลก", "เพชรบุรี", "เพชรบูรณ์", "แพร่", "ภูเก็ต",
    "มหาสารคาม", "มุกดาหาร", "แม่ฮ่องสอน", "ยโสธร", "ยะลา", "ร้อยเอ็ด", "ระนอง",
    "ระยอง", "ราชบุรี", "ลพบุรี", "ลำปาง", "ลำพูน", "เลย", "ศรีสะเกษ", "สกลนคร",
    "สงขลา", "สตูล", "สมุทรปราการ", "สมุทรสงคราม", "สมุทรสาคร", "สระแก้ว", "สระบุรี",
    "สิงห์บุรี", "สุโขทัย", "สุพรรณบุรี", "สุราษฎร์ธานี", "สุรินทร์", "หนองคาย",
    "หนองบัวลำภู", "อ่างทอง", "อำนาจเจริญ", "อุดรธานี", "อุตรดิตถ์", "อุทัยธานี", "อุบลราชธานี"
]

def has_province(label):
    return any(prov in label for prov in THAI_PROVINCES)

def remove_province(label):
    for prov in THAI_PROVINCES:
        label = label.replace(prov, "")
    return label.strip()

random.seed(RANDOM_SEED)

# --- Step 1: อ่านทุกบรรทัด ไม่ตัดทิ้ง ---
entries = []
count_with_prov    = 0
count_without_prov = 0

with open(ANNOTATION_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        filename   = parts[0]
        label_full = " ".join(parts[1:])

        if has_province(label_full):
            count_with_prov += 1
            label_plate = remove_province(label_full)
        else:
            count_without_prov += 1
            label_plate = label_full  # ไม่มีจังหวัด ใช้เหมือนเดิม

        entries.append((filename, label_full, label_plate))

total = len(entries)
print(f"รวมทั้งหมด: {total} รายการ")
print(f"  มีจังหวัด (ตัดออกแล้ว): {count_with_prov} รายการ")
print(f"  เลขทะเบียนอย่างเดียว:   {count_without_prov} รายการ")

# ตัวอย่าง
print(f"\nตัวอย่าง:")
for fn, lf, lp in entries[:3]:
    print(f"  {fn}")
    print(f"    labels_full.txt  → '{lf}'")
    print(f"    labels_plate.txt → '{lp}'")

# --- Step 2: Shuffle แล้วแบ่ง ---
random.shuffle(entries)

n_train = int(total * TRAIN_RATIO)
n_val   = int(total * VAL_RATIO)

splits = {
    "train": entries[:n_train],
    "val":   entries[n_train : n_train + n_val],
    "test":  entries[n_train + n_val:],
}

print()
for name, data in splits.items():
    print(f"  {name}: {len(data)} รูป")

# --- Step 3: Copy รูป + สร้าง 2 label files ต่อ split ---
print()
for split_name, split_entries in splits.items():
    split_dir = Path(OUTPUT_DIR) / split_name
    split_dir.mkdir(parents=True, exist_ok=True)

    lines_full  = []
    lines_plate = []
    missing = 0

    for filename, label_full, label_plate in split_entries:
        src = Path(IMAGE_DIR) / filename
        dst = split_dir / filename

        if src.exists():
            shutil.copy2(src, dst)
            if has_province(label_full):
                lines_full.append(f"{filename}\t{label_full}")
            lines_plate.append(f"{filename}\t{label_plate}")
        else:
            missing += 1
            print(f"  ⚠ ไม่เจอไฟล์: {filename}")

    # เขียน labels_full.txt
    with open(split_dir / "labels_full.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines_full))

    # เขียน labels_plate.txt
    with open(split_dir / "labels_plate.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines_plate))

    print(f" {split_name}: {len(lines_plate)} รูป"
          + (f" (ขาดหาย {missing} รูป)" if missing else ""))

print(f"\n เสร็จสิ้น! ไฟล์อยู่ที่: {OUTPUT_DIR}")
