# training/ — สคริปต์สร้าง dataset และ train โมเดล AI ของ SmartLPR

โฟลเดอร์นี้รวมโค้ดที่ใช้ "สร้าง" โมเดลทั้ง 3 ตัวที่ระบบ SmartLPR ใช้งาน
ย้ายมาจาก `MyProject\PlateDetection` (เอามาเฉพาะโค้ด **ไม่ได้เอา dataset มา** — dataset ยังอยู่ที่เดิม)

| โมเดล | หน้าที่ในระบบ | ไฟล์ที่ระบบใช้จริง |
|---|---|---|
| YOLO ป้ายทะเบียน | หาว่าป้ายอยู่ตรงไหนในรูป | `models/yolo/best.pt` |
| OCR | อ่านตัวอักษรบนป้าย (เลขทะเบียน + จังหวัด) | `models/ocr/thai_platev1.pth` + `thai_plate_chars.txt` |
| สีรถ | บอกว่ารถสีอะไร | `models/color/car_color_model.h5` + `class_names.json` |

## โครงสร้าง

```
training/
├── train_config.py          ← path ทั้งหมดอยู่ที่นี่ที่เดียว (ย้าย dataset ไปไหน แก้ไฟล์นี้)
├── requirements-train.txt   ← library ที่ต้องใช้ตอน train
├── outputs/                 ← ผลการ train ทั้งหมดจะมาอยู่ที่นี่ (ไม่ขึ้น git / ไม่เข้า docker)
├── yolo_plate/
│   └── train_yolo.py        ← (ใหม่) train YOLO หาป้าย จาก dataset Roboflow
├── ocr/
│   ├── dataset/             ← สร้าง dataset OCR (เดิมอยู่ PlateDetection\src)
│   ├── run_train.py         ← train ขั้นที่ 1: เลขทะเบียนอย่างเดียว
│   ├── run_trainfull.py     ← train ขั้นที่ 2: เลขทะเบียน + จังหวัด
│   ├── run_test.py          ← วัดความแม่นยำกับชุด test
│   └── deep_text_recognition/  ← โค้ด train ของ deep-text-recognition-benchmark (Apache-2.0)
└── color/
    ├── dataset/             ← เตรียมรูปรถ + แปะ label สี (เดิมอยู่ PlateDetection\main_code)
    └── train_car_color.py   ← train โมเดลแยกสี
```

## dataset อยู่ไหน

ทุกสคริปต์อ่าน path จาก `train_config.py` ค่าเริ่มต้นชี้ไปที่ `C:\Users\ACER\Downloads\MyProject\PlateDetection`
ถ้าย้าย dataset ไปที่อื่น เลือกได้ 2 ทาง:
- แก้ `DEFAULT_DATA_ROOT` ใน `train_config.py`
- หรือตั้ง environment variable ก่อนรัน: `set SMARTLPR_DATA_ROOT=D:\datasets\PlateDetection`

## วิธีติดตั้ง

```bat
pip install -r training\requirements-train.txt
```

---

## 1) YOLO หาป้ายทะเบียน

dataset ทำใน **Roboflow** แล้ว export เป็น YOLOv11 (เลยไม่มีสคริปต์สร้าง dataset ฝั่งนี้)

```bat
python training\yolo_plate\train_yolo.py
python training\yolo_plate\train_yolo.py --data "D:\path\to\thai_plate_detection.v4i.yolov11\data.yaml"
python training\yolo_plate\train_yolo.py --epochs 150 --batch 8
python training\yolo_plate\train_yolo.py --deploy     ← train เสร็จแล้วเอาไปใช้ในระบบทันที (สำรองไฟล์เก่าให้)
python training\yolo_plate\train_yolo.py --resume training\outputs\yolo\<ชื่อรอบ>\weights\last.pt
```

ผลอยู่ที่ `training\outputs\yolo\<ชื่อรอบ>\weights\best.pt` — สคริปต์จะวัดผลกับชุด test ให้อัตโนมัติตอนจบ

## 2) OCR อ่านป้ายทะเบียน

รันตามลำดับ (ทุกไฟล์อยู่ใน `training\ocr\dataset\` ยกเว้นที่บอกไว้)

| ลำดับ | ไฟล์ | ทำอะไร | ได้อะไรออกมา |
|---|---|---|---|
| 1 | `crop_img.py` | เลือกโฟลเดอร์รูปรถ → ใช้ YOLO หาป้ายแล้วตัดออกมา ขยาย 2.5 เท่า | `<โฟลเดอร์ที่เลือก>\Cropped_Plates\*.png` |
| 2 | `create_labels.py` | โปรแกรม GUI โชว์รูปป้ายทีละรูป ให้พิมพ์เฉลย | `labels.csv` |
| 3 | `create.py` | คัดเฉพาะรูปที่พิมพ์เฉลยแล้ว | `labels_clean.csv` |
| 4 | `create_train_list.py` | แปลง CSV เป็นรูปแบบ `ชื่อไฟล์ เลขทะเบียน จังหวัด` | `train_list.txt` |
| 5 | `split.py` | สุ่มแบ่ง train 85% / val 10% / test 5% และแยก label 2 แบบ | `dataset_split\{train,val,test}\labels_plate.txt, labels_full.txt` |
| 6 | `check_dataset.py` | เช็คว่า label มีตัวอักษรที่ไม่อยู่ใน `thai_plate_chars.txt` ไหม | (แค่ print) |
| 7 | `create_lmdb.py` | แปลงเป็น lmdb (เลขทะเบียนอย่างเดียว) | `data_lmdb\` |
| 8 | `create_lmdb_full.py` | แปลงเป็น lmdb (เลขทะเบียน + จังหวัด) | `data_lmdb_full\` |
| 9 | `ocr\run_train.py` | train ขั้นที่ 1 เริ่มจาก `thai.pth` | `outputs\ocr\saved_models\thai_plate_ocr\` |
| 10 | `ocr\run_trainfull.py` | train ขั้นที่ 2 ต่อจากขั้นที่ 1 | `outputs\ocr\saved_models\thai_plate_ocr_full\` |
| 11 | `ocr\run_test.py` | วัดความแม่นยำกับชุด test | `outputs\ocr\result\` |

ทำไมต้อง train 2 ขั้น: เลขทะเบียนสั้นและมีข้อมูลเยอะ ให้โมเดลเรียนตัวอักษรให้แม่นก่อน
แล้วค่อยสอนให้อ่านชื่อจังหวัดที่ยาวกว่า (ขั้นที่ 2 จึงตั้ง `--batch_max_length 34`)

`create_char_list.py` เป็นตัวช่วยสร้างรายการตัวอักษรแบบพื้นฐาน (ไม่มีสระ) เขียนไว้ที่ `outputs\` เท่านั้น
**ห้ามเอาไปทับ `thai_plate_chars.txt` ของระบบ** เพราะโมเดลที่ใช้อยู่ train ด้วยชุดที่มีสระ/วรรณยุกต์ ถ้าชุดตัวอักษรไม่ตรง โมเดลจะอ่านเพี้ยนทั้งหมด

ทดสอบโมเดลที่ระบบใช้อยู่ตอนนี้: `python training\ocr\run_test.py models\ocr\thai_platev1.pth`

## 3) โมเดลแยกสีรถ

| ลำดับ | ไฟล์ | ทำอะไร |
|---|---|---|
| 1 | `color\dataset\car_crop.py` | ใช้ YOLO (yolo11n) หารถในรูปดิบจากกล้อง แล้ว crop ทีละคัน |
| 2 | `color\dataset\lebel_color.py` | โปรแกรม GUI กดปุ่มเลือกสี → ย้ายรูปไปโฟลเดอร์ของสีนั้น |
| 3 | `color\train_car_color.py` | train EfficientNetB0 จาก `archive\{train,val,test}\<สี>\` |

ผลอยู่ที่ `training\outputs\color\` (โมเดล, class_names.json, กราฟ, confusion matrix)
พอใจแล้วค่อย copy `car_color_model.h5` + `class_names.json` ไปทับใน `models\color\` (ต้องคู่กันเสมอ)

> ⚠️ ชื่อสีใน `lebel_color.py` (11 สี มี beige, silver) ยังไม่ตรงกับ 15 class ของ dataset VCoR ใน `archive\`
> ถ้าจะเอารูปที่ label เองไปรวมเทรน ต้องปรับชื่อโฟลเดอร์สีให้ตรงกันก่อน

---

## สิ่งที่เปลี่ยนจากต้นฉบับใน PlateDetection

- path ทั้งหมดย้ายไปอยู่ `train_config.py` (ต้นฉบับบางตัวชี้ไปที่ที่ไม่มีแล้ว เช่น `PlateDetection\models\best.pt` และ `PlateDetection\thai_plate_chars.txt`)
- `crop_img.py` / `car_crop.py` ใช้โมเดลจาก `models\` ของ SmartLPR
- OCR ใช้ `thai_plate_chars.txt` ของ SmartLPR (ไฟล์เดียวกับที่ระบบใช้อ่านป้ายจริง)
- `run_trainfull.py` ใช้ชื่อ `thai_plate_ocr_full` (เดิมใช้ชื่อเดียวกับขั้นที่ 1 เลยเขียนทับกัน)
- `create_train_list.py` ข้ามบรรทัดหัวตาราง CSV (เดิมหลุดเข้าไปเป็นบรรทัด `filename text done`)
- ผลการ train ทุกตัวไปอยู่ `training\outputs\` ไม่ไปทับโมเดลใน `models\` โดยไม่ตั้งใจ

## ไม่ได้ย้ายมา (ตั้งใจ)

- dataset ทุกชุด: `archive\`, `Cropped_Plates\`, `dataset_split\`, `data_lmdb*\`, `thai_plate_detection.v3i.yolov11\`, `labels*.csv`, `train_list.txt`
- ไฟล์โมเดลใหญ่: `thai.pth` (215MB), `saved_models\`, `custom_model\`, `runs\` — สคริปต์ยังอ่านจาก PlateDetection ได้
- สคริปต์ทดสอบ/รันระบบ ที่ไม่เกี่ยวกับการ train: `main_rtsp*.py`, `test_model*.py`, `webhook01.py`, `mock_server.py`, `hsv_color_picker.py` ฯลฯ
