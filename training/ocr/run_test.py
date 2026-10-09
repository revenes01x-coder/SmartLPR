import subprocess
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

# ทดสอบความแม่นยำ OCR กับชุด test (data_lmdb_full/test — ชุดที่โมเดลไม่เคยเห็นตอน train)
# ใช้โมเดลล่าสุดที่หาเจอ: ขั้นที่ 2 ใหม่ -> ขั้นที่ 1 ใหม่ -> ผลเก่าใน PlateDetection
# อยากทดสอบไฟล์อื่น (เช่น models/ocr/thai_platev1.pth) ส่ง path เป็น argument ได้:
#   python run_test.py ..\..\models\ocr\thai_platev1.pth
TEST_DATA  = cfg.OCR_LMDB_FULL_DIR / "test"
CHAR_FILE  = cfg.CHAR_FILE
if len(sys.argv) > 1:
    MODEL_PATH = Path(sys.argv[1]).resolve()
else:
    MODEL_PATH = cfg.first_existing(
        cfg.OCR_OUTPUT_DIR / "saved_models" / cfg.OCR_EXP_NAME_FULL / "best_accuracy.pth",
        cfg.OCR_OUTPUT_DIR / "saved_models" / cfg.OCR_EXP_NAME / "best_accuracy.pth",
        cfg.OCR_LEGACY_SAVED / cfg.OCR_EXP_NAME / "best_accuracy.pth",
    )
TEST_PY    = cfg.DTRB_DIR / "test.py"

if not Path(TEST_DATA).exists():
    sys.exit(f"❌ ไม่เจอ {TEST_DATA} — เช็ค path ใน training/train_config.py")
if not Path(CHAR_FILE).exists():
    sys.exit(f"❌ ไม่เจอ {CHAR_FILE} — เช็ค path ใน training/train_config.py")
if not Path(MODEL_PATH).exists():
    sys.exit(f"❌ ไม่เจอ {MODEL_PATH} — เช็ค path ใน training/train_config.py")

with open(CHAR_FILE, encoding="utf-8") as f:
    characters = f.read().rstrip('\n').rstrip('\r')

args = [
    sys.executable, str(TEST_PY),
    "--eval_data",       str(TEST_DATA),
    "--saved_model",     str(MODEL_PATH),
    "--Transformation",  "TPS",
    "--FeatureExtraction", "ResNet",
    "--SequenceModeling",  "BiLSTM",
    "--Prediction",      "Attn",
    "--character",       characters,
    "--hidden_size",     "512",
    "--workers",         "0",
]

print("===== ทดสอบ Model =====")
print(f"Test data  : {TEST_DATA}")
print(f"Model path : {MODEL_PATH}")
print("=======================\n")

# train.py / test.py ของ deep-text-recognition-benchmark เซฟผลลัพธ์แบบ relative path (./saved_models, ./result)
# เลยเปลี่ยน working directory ไปที่ training/outputs/ocr ก่อน ผลลัพธ์จะได้ไปรวมอยู่ที่นั่น ไม่ปนกับโค้ด
cfg.OCR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
os.chdir(cfg.OCR_OUTPUT_DIR)
subprocess.run(args)
