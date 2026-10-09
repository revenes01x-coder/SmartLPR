# create.py — คัดเฉพาะแถวที่พิมพ์เฉลยเสร็จแล้ว (done == YES) จาก labels.csv -> labels_clean.csv
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ให้ import train_config.py (อยู่ในโฟลเดอร์ training) ได้
import train_config as cfg

df = pd.read_csv(cfg.OCR_LABELS_CSV)
df_done = df[df['done'] == 'YES']
df_done.to_csv(cfg.OCR_LABELS_CLEAN_CSV, index=False)
print(f"พร้อม train: {len(df_done)} ใบ")