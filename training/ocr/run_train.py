import subprocess
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

# ขั้นที่ 1: train OCR ด้วย dataset "เลขทะเบียนอย่างเดียว" (data_lmdb) โดยเริ่มจากโมเดลภาษาไทย thai.pth
TRAIN_DATA = cfg.OCR_LMDB_DIR / "train"
VALID_DATA = cfg.OCR_LMDB_DIR / "val"
CHAR_FILE  = cfg.CHAR_FILE
PRETRAINED = cfg.OCR_PRETRAINED
TRAIN_PY   = cfg.DTRB_DIR / "train.py"

if not Path(TRAIN_DATA).exists():
    sys.exit(f"❌ ไม่เจอ {TRAIN_DATA} — เช็ค path ใน training/train_config.py")
if not Path(VALID_DATA).exists():
    sys.exit(f"❌ ไม่เจอ {VALID_DATA} — เช็ค path ใน training/train_config.py")
if not Path(CHAR_FILE).exists():
    sys.exit(f"❌ ไม่เจอ {CHAR_FILE} — เช็ค path ใน training/train_config.py")
if not Path(PRETRAINED).exists():
    sys.exit(f"❌ ไม่เจอ {PRETRAINED} — เช็ค path ใน training/train_config.py")

with open(CHAR_FILE, encoding="utf-8") as f:
    characters = f.read().rstrip('\n').rstrip('\r')

args = [
    sys.executable, str(TRAIN_PY),
    "--train_data",      str(TRAIN_DATA),
    "--valid_data",      str(VALID_DATA),
    "--select_data",     "/",
    "--batch_ratio",     "1",
    "--Transformation",  "TPS",
    "--FeatureExtraction", "ResNet",
    "--SequenceModeling",  "BiLSTM",
    "--Prediction",      "Attn",
    "--saved_model",     str(PRETRAINED),
    "--FT",
    "--character",       characters,
    "--batch_size",      "64",
    "--num_iter",        "10000",
    "--valInterval",     "500",
    "--exp_name",        cfg.OCR_EXP_NAME,
    "--workers",         "0",
    "--hidden_size",     "512",
]

print("===== เริ่ม Train OCR (เลขทะเบียน) =====")
print(f"Train data : {TRAIN_DATA}")
print(f"Valid data : {VALID_DATA}")
print(f"Char file  : {CHAR_FILE}")
print(f"Pretrained : {PRETRAINED}")
print(f"Output     : {cfg.OCR_OUTPUT_DIR / 'saved_models' / cfg.OCR_EXP_NAME}")
print("================================\n")

# train.py / test.py ของ deep-text-recognition-benchmark เซฟผลลัพธ์แบบ relative path (./saved_models, ./result)
# เลยเปลี่ยน working directory ไปที่ training/outputs/ocr ก่อน ผลลัพธ์จะได้ไปรวมอยู่ที่นั่น ไม่ปนกับโค้ด
cfg.OCR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
os.chdir(cfg.OCR_OUTPUT_DIR)
subprocess.run(args)
