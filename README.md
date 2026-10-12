# SmartLPR

ระบบอ่านป้ายทะเบียนรถจากกล้อง RTSP แล้วส่งผล (ทะเบียน จังหวัด สีรถ และรูป) ไปยัง webhook ของลูกค้า

**สถานะ:** พร้อมใช้งานจริง (Production)

## ระบบทำงานอย่างไร

ระบบแยกเป็น **3 โปรเจค** (โฟลเดอร์ละโปรเจค แต่ละอันมี `docker-compose.yml` ของตัวเอง) รวม **4 คอนเทนเนอร์**

```
ผู้ใช้ ──HTTPS──► [frontend: nginx] ──/api/──► [backend: FastAPI + webhook worker] ──► webhook ของลูกค้า
                                                   ▲              │
                     POST /capture-event ──────────┘              ▼
กล้อง RTSP ──► [camera: AI อ่านป้าย] ──── อ่านรายชื่อกล้อง ────► [db: PostgreSQL]
                     │                                            ▲
                     └──► captures/ (โฟลเดอร์รูปที่ใช้ร่วมกัน) ◄── backend เปิดรูปไปแนบ webhook
```

| โปรเจค | คอนเทนเนอร์ | หน้าที่ | พอร์ต |
| --- | --- | --- | --- |
| `backend/` | `smartlpr-backend` | API (FastAPI) และงานเบื้องหลังที่ส่ง webhook | 8000 ภายในเท่านั้น |
| `backend/` | `smartlpr-db` | ฐานข้อมูล PostgreSQL 15 | ไม่เปิดออกนอกเครื่อง |
| `camera/` | `smartlpr-camera` | ดึงภาพจากกล้อง ตรวจรถ หาป้าย อ่านตัวอักษร ทายสีรถ | ไม่มี |
| `frontend/` | `smartlpr-frontend` | nginx แจกหน้าเว็บ และส่งต่อ `/api/` ไป backend | 8081 |

**คุยกันข้ามโปรเจคยังไง:** คอนเทนเนอร์ที่อยู่คนละ docker compose ปกติมองไม่เห็นกัน จึงมี network กลาง 2 เส้นที่สร้างครั้งเดียว (`scripts/setup-networks.sh`) แล้วทุกโปรเจคต่อเข้ามา

| network | ใครต่ออยู่ | ใช้ทำอะไร |
| --- | --- | --- |
| `smartlpr_web` (หน้าบ้าน) | frontend, backend, camera | nginx ส่งต่อ `/api/` ไปที่ `backend:8000`, camera ส่ง event ไปที่ `backend:8000/capture-event` |
| `smartlpr_db` (หลังบ้าน) | backend, camera, db | backend อ่าน/เขียน DB, camera อ่านรายชื่อกล้อง |

frontend ไม่อยู่ใน `smartlpr_db` จึงมองไม่เห็นฐานข้อมูล

พาร์ทเนอร์ที่เรียก API จากระบบของตัวเอง (เช่น `/partner/cameras`) ยืนยันตัวด้วย header `X-API-Key` ของผู้ใช้แต่ละคน ผู้ใช้ต้องส่งคำขอใช้งานจากหน้าเว็บและได้รับอนุมัติจากผู้ดูแลก่อน จึงจะสร้าง API key ได้

## โครงสร้างโปรเจกต์

```
SmartLPR/
├── backend/                  โปรเจค 1: API + webhook worker + PostgreSQL
│   ├── app/                  โค้ด (ถูกวางไว้ที่ /app ในคอนเทนเนอร์)
│   │   ├── smartlpr/         แกนของ backend: main.py, config.py, models.py (ตาราง DB), security.py
│   │   ├── routers/          ประกาศ endpoint แยกตามกลุ่ม
│   │   ├── services/         ตรรกะของแต่ละกลุ่ม รวมถึงอีเมล rate limit และ audit log
│   │   ├── security/         ตรวจ URL ของ webhook และกล้องไม่ให้ชี้เข้าเครือข่ายภายใน
│   │   ├── assets/           รูปที่ใช้ส่งทดสอบตอนผู้ใช้เพิ่ม webhook
│   │   ├── worker.py         งานเบื้องหลัง (ส่ง webhook, ตรวจกล้องใหม่, ลบข้อมูลตามอายุ)
│   │   └── make_admin.py     ตั้ง/ถอดสิทธิ์ผู้ดูแล
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── docker-compose.yml    db + backend
│   └── .env.example
├── camera/                   โปรเจค 2: AI อ่านป้ายทะเบียน
│   ├── app/
│   │   ├── camera/           camera_manager / camera_streamer / camera_worker / plate_ocr
│   │   │   ├── config.py     ค่าตั้งเฉพาะที่ camera ใช้
│   │   │   └── db.py         query เดียวที่ camera ใช้: ดึงรายชื่อกล้องที่ต้องเฝ้า
│   │   ├── security/         ตรวจ URL ของกล้อง (สำเนาจาก backend เฉพาะ 2 ไฟล์ที่ใช้)
│   │   ├── plate_detection_repo/   โค้ดโครงข่าย OCR
│   │   ├── models/           ไฟล์โมเดล AI (ไม่อยู่ใน git)
│   │   └── thai_plate_chars.txt
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── docker-compose.yml
│   └── .env.example
├── frontend/                 โปรเจค 3: หน้าเว็บ
│   ├── public/               index.html, user-guide.pdf (เฉพาะโฟลเดอร์นี้ที่ถูกแจกออกเว็บ)
│   ├── nginx.conf
│   └── docker-compose.yml
├── scripts/setup-networks.sh สร้าง network กลาง (รันซ้ำได้)
├── docker-compose.yml        ไฟล์รวมสำหรับ VM: include ทั้ง 3 โปรเจค = 4 คอนเทนเนอร์
├── captures/  logs/          ข้อมูลตอนรัน ใช้ร่วมกันระหว่าง backend กับ camera (ไม่อยู่ใน git)
└── training/                 สคริปต์ train โมเดล (ใช้บนเครื่องผู้พัฒนาเท่านั้น)
```

**ทำไม camera มี `db.py` ของตัวเอง:** camera ใช้ฐานข้อมูลแค่ query เดียว (รายชื่อกล้องที่ต้องเฝ้า) จึงเขียน SQL ไว้ในไฟล์เล็กๆ ไฟล์เดียว แทนการ copy `models.py` ทั้งไฟล์ของ backend มา backend จะแก้ตารางอื่นยังไงก็ไม่กระทบ camera ต้องแก้ `camera/app/camera/db.py` ตามก็ต่อเมื่อเปลี่ยนชื่อคอลัมน์ที่ query นี้ใช้เท่านั้น

## สิ่งที่ต้องมีก่อนติดตั้ง

- Docker และ Docker Compose v2.20 ขึ้นไป (ไฟล์รวมใช้ `include`)
- ไฟล์โมเดล AI (ไม่อยู่ใน git เพราะไฟล์ใหญ่ ต้องขอจากผู้ดูแลโปรเจกต์ หรือ train เองด้วยสคริปต์ใน `training/`) วางตามโครงสร้างนี้

  ```
  camera/app/models/
  ├── detector/yolo11n.pt          ตรวจหารถ
  ├── yolo/best.pt                 หากรอบป้ายทะเบียน
  ├── ocr/thai_platev1.pth         อ่านตัวอักษรบนป้าย
  └── color/
      ├── car_color_model.h5       ทายสีรถ
      └── class_names.json
  ```

- บัญชี Gmail พร้อมรหัสผ่านแอป สำหรับส่งอีเมล OTP

## รันในเครื่อง (3 โปรเจคแยกกัน)

1. สร้างไฟล์ตั้งค่าของแต่ละโปรเจค แล้วแก้ค่าที่อยู่ในเครื่องหมาย `<...>`

   ```bash
   cp backend/.env.example backend/.env
   cp camera/.env.example camera/.env
   ```

   ค่าเหล่านี้ต้องตรงกันทั้งสองไฟล์: `CAPTURE_EVENT_SECRET`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `CAPTURES_SAVE_DIR`

2. สร้าง network กลาง (ครั้งแรกครั้งเดียว)

   ```bash
   sh scripts/setup-networks.sh
   ```

   บน Windows (PowerShell) พิมพ์ 2 คำสั่งนี้แทน

   ```powershell
   docker network create --driver bridge --subnet 172.20.0.0/16 --gateway 172.20.0.1 smartlpr_web
   docker network create --driver bridge smartlpr_db
   ```

3. เปิดทีละโปรเจค (เริ่มที่ backend เพราะมี DB อยู่ข้างใน)

   ```bash
   cd backend  && docker compose up -d --build && cd ..
   cd camera   && docker compose up -d --build && cd ..
   cd frontend && docker compose up -d && cd ..
   ```

   เปิดผิดลำดับก็ไม่พัง: camera จะลองต่อ DB ใหม่ทุก 5 วินาทีเอง ส่วน nginx เปิดได้แม้ backend ยังไม่มา (ระหว่างนั้น `/api/` ตอบ 502)

4. เปิดหน้าเว็บที่ `http://localhost:8081` หรือเปิดให้คนนอกเข้าผ่าน ngrok

   ```bash
   ngrok http 8081
   ```

   ชี้ ngrok ไปที่ 8081 (nginx) ไม่ใช่ 8000 เพราะหน้าเว็บเรียก `/api/` ผ่าน nginx เข้าผ่าน ngrok (https) ให้ตั้ง `COOKIE_SECURE=true` ใน `backend/.env` เข้าผ่าน `http://localhost:8081` ให้ตั้ง `false`

5. สมัครบัญชีแรก ยืนยัน OTP ทางอีเมล แล้วตั้งเป็นผู้ดูแล

   ```bash
   cd backend && docker compose exec backend python make_admin.py <อีเมล>
   ```

6. เข้าสู่ระบบด้วยบัญชีผู้ดูแล แล้วเพิ่มช่องทางติดต่อที่หน้า "ติดต่อเรา" (ระบบไม่สร้างช่องทางเริ่มต้นให้)

ตารางในฐานข้อมูลถูกสร้างอัตโนมัติเมื่อ backend เริ่มทำงานครั้งแรก

**แก้โค้ดแล้วต้องทำอะไร:** backend รีโหลดเองเมื่อไฟล์ `.py` เปลี่ยน (โหมด DEV) camera ต้อง `docker compose restart camera` frontend แก้ `public/` เห็นผลทันที แก้ `nginx.conf` ต้อง `docker compose restart frontend`

## รันบน VM (ไฟล์รวม 4 คอนเทนเนอร์)

ไฟล์ `docker-compose.yml` ชั้นนอกสุด `include` ทั้ง 3 โปรเจคเข้ามา สั่งคำสั่งเดียวได้ครบ ค่าตั้งจริงยังอยู่ในโฟลเดอร์ของแต่ละโปรเจค (แก้ที่เดียว ได้ผลทั้งในเครื่องและบน VM)

```bash
sh scripts/setup-networks.sh
docker compose up -d --build
```

ในเครื่องเดียวกัน ให้ใช้ "ไฟล์รวม" หรือ "3 ไฟล์แยก" อย่างใดอย่างหนึ่ง (ชื่อคอนเทนเนอร์ซ้ำกัน) ถ้าจะสลับ ให้ `docker compose down` ชุดเดิมก่อน

### ย้ายจากโครงสร้างเดิม (ทำครั้งเดียวบน VM ก่อน merge เข้า main)

ทำ "ก่อน" merge เพราะต้องปิดระบบเดิมด้วยไฟล์ compose เดิม (network ชุดเดิมใช้ subnet `172.20.0.0/16` เดียวกับชุดใหม่ ต้องลบของเดิมก่อนถึงจะสร้างใหม่ได้) ข้อมูลใน DB และโมเดลไม่หาย เพราะ volume ใหม่ตั้งชื่อตรงกับของเดิม (`smartlpr_db_data`, `smartlpr_model_data`)

```bash
cd ~/smartlpr

# 0) เช็คชื่อ volume เดิม — ต้องเห็น smartlpr_db_data และ smartlpr_model_data
docker volume ls | grep smartlpr

# 1) สำรองฐานข้อมูล
docker exec smartlpr-db pg_dump -U smartlpr_user smartlpr_db > ~/smartlpr_backup_$(date +%F).sql

# 2) ปิดระบบเดิม (ไม่ใช้ -v เด็ดขาด — volume/ข้อมูลยังอยู่)
docker compose down

# 3) ดึงโค้ดโครงสร้างใหม่
git fetch origin
git checkout restructure/microservices

# 4) แยก .env ให้แต่ละโปรเจค (ใช้ไฟล์เดิมได้เลย ค่าที่เกินมาไม่มีผล)
cp .env backend/.env
cp .env camera/.env
#    ย้ายไฟล์โมเดลไปไว้ในโปรเจค camera (camera ใช้ volume เดิมอยู่แล้ว ข้อนี้ทำให้โครงสร้างตรงกับในเครื่อง)
[ -d models ] && mv models camera/app/models

# 5) สร้าง network กลาง แล้วเปิดทั้งหมด
sh scripts/setup-networks.sh
docker compose up -d --build

# 6) เช็ค
docker compose ps
docker logs smartlpr-frontend --tail 3
```

ใช้งานได้แล้วค่อย merge `restructure/microservices` เข้า `main` บน GitHub (deploy อัตโนมัติจะทำงานต่อได้ตามปกติ) แล้วกลับมาอยู่ที่ main บน VM: `git checkout -B main origin/main`

ถ้ามีปัญหาและต้องการกลับไปใช้ระบบเดิม: `docker compose down` → `git checkout main` → `docker network rm smartlpr_web smartlpr_db` → `docker compose up -d --build`

## ข้อควรรู้เมื่อใช้งานจริง

- **HTTPS:** ให้บริการผ่าน reverse proxy ที่ทำ HTTPS แล้วส่งต่อมาที่พอร์ต 8081 และตั้ง `COOKIE_SECURE=true` ใน `backend/.env`
- **IP จริงของผู้ใช้:** บน VM request วิ่งผ่าน proxy HTTPS ของบริษัท (`192.168.100.44`) → nginx ของเพื่อน → nginx ของเรา `frontend/nginx.conf` เชื่อ `X-Forwarded-For` จาก `172.20.0.1` (gateway ของ `smartlpr_web` ที่ล็อกไว้ใน `scripts/setup-networks.sh`) และ `192.168.100.44` แล้วใช้ `real_ip_recursive on` หยิบ IP จริง ถ้าเปลี่ยน subnet ต้องแก้ทั้งสองไฟล์ให้ตรงกัน และ nginx ของเพื่อน (block `/smartlpr/`) ต้องใช้ `X-Forwarded-For $proxy_add_x_forwarded_for` ไม่เช่นนั้นผู้ใช้ทุกคนจะถูกนับเป็น IP เดียวกันและโดน rate limit พร้อมกัน
- **ห้ามเปลี่ยน `SECRET_KEY` หลังเปิดใช้งาน:** API key, OTP และ refresh token ถูกเก็บเป็น hash ที่ผูกกับคีย์นี้
- **ห้ามใช้ `docker compose down -v`:** ตัวเลือก `-v` ลบ volume `smartlpr_db_data` ซึ่งเป็นฐานข้อมูลทั้งหมด
- **เปลี่ยนไฟล์โมเดล:** คอนเทนเนอร์ camera อ่านโมเดลจาก volume `smartlpr_model_data` ซึ่งถูกคัดลอกจาก `camera/app/models/` ตอนสร้างครั้งแรกเท่านั้น เมื่อเปลี่ยนไฟล์โมเดลต้องลบ volume นี้แล้ว build ใหม่ (`docker compose down` → `docker volume rm smartlpr_model_data` → `docker compose up -d --build`)
- **`/capture-event` ใช้ภายในเท่านั้น:** camera ยิงตรงไปที่ `backend:8000` ผ่าน network กลาง nginx ปิด `/api/capture-event` ไม่ให้คนนอกเรียก
- **โค้ดบนเซิร์ฟเวอร์:** คอนเทนเนอร์ backend และ camera อ่านโค้ดจากโฟลเดอร์โปรเจกต์โดยตรง (โหมด DEV) จึงไม่ควรแก้ไฟล์บนเซิร์ฟเวอร์โดยตรง ให้แก้ผ่าน git แล้ว deploy

## งานเบื้องหลังที่ระบบทำเอง

backend รันงานเหล่านี้อัตโนมัติ (ดูใน `backend/app/worker.py`)

**การส่ง webhook**

- ส่งทันทีที่มี event ใหม่: `/capture-event` บันทึก event ลง DB แล้ว "กดกริ่ง" ปลุกลูปส่ง webhook ทันที (ไม่ต้องรอรอบเช็คเหมือนเดิมที่เช็คทุก 1 วินาที) และลูปนี้ตื่นมาเช็คเองทุก 10 วินาที เพื่อเก็บงาน retry
- ปลายทางต้องตอบ HTTP 2xx พร้อม JSON ที่มี `event_id` ตรงกับที่ได้รับ จึงจะนับว่าส่งสำเร็จ
- ถ้าส่งไม่สำเร็จ ระบบลองใหม่อีก 3 ครั้ง ห่างกัน 3, 5 และ 10 นาที ถ้ายังไม่สำเร็จ event จะถูกย้ายไปพักเป็น dead letter
- ถ้าปลายทางเดียวกันมี dead letter ติดกัน 3 รายการ ระบบจะหยุดส่งไปที่ปลายทางนั้นชั่วคราวและส่งอีเมลแจ้งเจ้าของ
- ทุก 30 นาทีระบบเช็กว่าปลายทางที่ถูกหยุดกลับมาตอบได้หรือยัง ถ้าได้แล้วจะส่ง event ที่ค้างให้ต่อ

**การตรวจกล้องใหม่**

- ทุก 20 วินาที ระบบลองเชื่อมต่อ RTSP ของกล้องที่เพิ่งเพิ่ม ถ้าล้มเหลวครบ 3 ครั้ง กล้องตัวนั้นจะถูกลบออกจากระบบ และผู้ใช้ต้องเพิ่มใหม่

**การลบข้อมูลตามอายุ**

| ข้อมูล | ลบเมื่อ |
| --- | --- |
| รายการ event (ทะเบียนรถ) | เก่าเกิน 30 วัน ถูกซ่อนจากระบบ (soft delete) |
| บัญชีที่สมัครแล้วไม่ยืนยัน OTP | เกิน 24 ชั่วโมง |
| ประวัติ OTP | เกิน 7 วันหลังหมดอายุ |
| audit log ของผู้ดูแล | เกิน 365 วัน (ปรับได้ด้วย `ADMIN_AUDIT_LOG_RETENTION_DAYS`) |
| รูปและ log ของกล้องที่ถูกลบไปแล้ว | ตรวจและลบทุก 24 ชั่วโมง และทุกครั้งที่ backend เริ่มทำงาน |

ไฟล์รูปใน `captures/` และไฟล์ log ของกล้องที่ยังใช้งานอยู่ไม่ถูกลบตามอายุ จึงควรตรวจพื้นที่ดิสก์เป็นระยะ

## คำสั่งที่ใช้บ่อย

คำสั่งด้านล่างใช้กับไฟล์รวม (รันที่โฟลเดอร์นอกสุด) ถ้ารันแยกโปรเจค ให้ `cd` เข้าโฟลเดอร์ของโปรเจคนั้นก่อน

| งาน | คำสั่ง |
| --- | --- |
| เริ่มระบบ | `docker compose up -d` |
| หยุดระบบ | `docker compose down` |
| ดู log | `docker compose logs -f backend` (หรือ `camera`, `frontend`) |
| ตั้งผู้ดูแล | `docker compose exec backend python make_admin.py <อีเมล>` |
| ถอดสิทธิ์ผู้ดูแล | `docker compose exec backend python make_admin.py <อีเมล> --revoke` |
| สำรองฐานข้อมูล | `docker compose exec -T db pg_dump -U <DB_USER> <DB_NAME> > backup.sql` |
| กู้คืนฐานข้อมูล | `docker compose exec -T db psql -U <DB_USER> <DB_NAME> < backup.sql` |

เอกสาร API แบบ Swagger อยู่ที่ `http://localhost:8081/docs`

## ข้อมูลที่ระบบสร้างตอนรัน

| ตำแหน่ง | เนื้อหา |
| --- | --- |
| `captures/camera_{id}/` | รูปเต็ม (`full`) และรูปป้ายที่ตัดแล้ว (`crop`) ถูกลบเมื่อกล้องตัวนั้นถูกลบออกจากระบบ (camera เขียน backend อ่าน) |
| `logs/` | log ของกล้องแต่ละตัวและของ AI worker |
| volume `smartlpr_db_data` | ไฟล์ฐานข้อมูล |
| volume `smartlpr_model_data` | สำเนาโมเดลที่คอนเทนเนอร์ camera ใช้ |

ทั้งหมดนี้ไม่อยู่ใน git

## การ train โมเดล

โฟลเดอร์ `training/` เก็บโค้ดที่ใช้สร้างโมเดลทั้ง 3 ตัวของระบบ (YOLO หาป้าย, OCR อ่านป้าย, แยกสีรถ) ใช้บนเครื่องของผู้พัฒนาเท่านั้น ไม่ถูกใส่เข้า Docker image

- ติดตั้ง library: `pip install -r training/requirements-train.txt`
- dataset ไม่อยู่ใน git ตั้งตำแหน่งได้ที่ `training/train_config.py` หรือ environment variable `SMARTLPR_DATA_ROOT`
- ผลการ train ไปอยู่ที่ `training/outputs/` (ไม่ขึ้น git) จะไม่ไปทับโมเดลใน `camera/app/models/` เอง

ขั้นตอนละเอียดของแต่ละโมเดลอยู่ใน [`training/README.md`](training/README.md)

## การ deploy

การ push เข้า branch `main` จะสั่ง GitHub Actions (`.github/workflows/deploy.yml`) ให้ดึงโค้ดล่าสุด สร้าง network กลาง (ถ้ายังไม่มี) และ build คอนเทนเนอร์ใหม่บน VM ผ่านไฟล์รวมโดยอัตโนมัติ

ไฟล์ `backend/.env`, `camera/.env` และไฟล์โมเดลไม่ได้มากับ git จึงต้องวางบน VM ด้วยมือ

### ติด tag เวอร์ชัน

การ push tag อย่างเดียวไม่ทำให้ deploy (workflow ทำงานเฉพาะเมื่อ push เข้า `main`)

```bash
git tag -a v1.0.0 -m "SmartLPR v1.0.0"
git push origin v1.0.0
```

ตั้งชื่อตามแบบ `vMAJOR.MINOR.PATCH`: แก้บั๊กขยับเลขท้าย, เพิ่มฟีเจอร์ขยับเลขกลาง, เปลี่ยนจนของเดิมใช้ไม่ได้ขยับเลขหน้า

## แก้ปัญหาเบื้องต้น

| อาการ | สาเหตุที่พบบ่อย |
| --- | --- |
| `network smartlpr_web declared as external, but could not be found` | ยังไม่ได้รัน `scripts/setup-networks.sh` |
| `Pool overlaps with other one on this address space` ตอนสร้าง network | network ของโครงสร้างเดิม (subnet เดียวกัน) ยังอยู่ ปิดระบบเดิมด้วย `docker compose down` ก่อน (ดู "ย้ายจากโครงสร้างเดิม") |
| `env file .../backend/.env not found` | ยังไม่ได้สร้าง `backend/.env` หรือ `camera/.env` |
| `container name "/smartlpr-db" is already in use` | เปิดทั้ง 3 ไฟล์แยกและไฟล์รวมพร้อมกัน ให้ `docker compose down` ชุดหนึ่งก่อน |
| backend ไม่ขึ้น แจ้งว่า "ไม่พบ Environment Variable" | ยังไม่ได้ตั้งค่าตัวแปรนั้นใน `backend/.env` |
| camera ไม่ขึ้น | path ของโมเดลใน `camera/.env` ไม่ตรง หรือยังไม่ได้วางไฟล์โมเดลใน `camera/app/models/` |
| camera ส่ง event แล้วได้ 401 | `CAPTURE_EVENT_SECRET` ใน `backend/.env` กับ `camera/.env` ไม่ตรงกัน |
| หน้าเว็บขึ้น แต่เรียก API ได้ 502 | backend ยังไม่เปิด หรือยังเปิดไม่เสร็จ (`docker compose logs backend`) |
| เปลี่ยนโมเดลแล้วผลไม่เปลี่ยน | ยังไม่ได้ลบ volume `smartlpr_model_data` (ดู "ข้อควรรู้เมื่อใช้งานจริง") |
| ไม่ได้รับอีเมล OTP | `SMTP_USER` หรือ `SMTP_APP_PASSWORD` ไม่ถูกต้อง |
| login หลุดทุก 15 นาที | ตั้ง `COOKIE_SECURE=true` แต่เข้าเว็บผ่าน http |
| login แล้วกดรีเฟรชหน้าแล้วหลุด | เข้าเว็บผ่าน sub-path (เช่น `/smartlpr/`) แต่ไม่ได้ตั้ง `REFRESH_COOKIE_PATH` |
| เพิ่มกล้องหรือ webhook ในวง LAN ไม่ได้ | `ALLOW_PRIVATE_IP=false` กัน IP ภายในไว้ |
| กล้องที่เพิ่งเพิ่มหายไปเอง | ระบบเชื่อมต่อ RTSP ไม่ได้ครบ 3 ครั้ง ตรวจ URL ชื่อผู้ใช้ และรหัสผ่านของกล้อง แล้วเพิ่มใหม่ |
| ลูกค้าไม่ได้รับ webhook | ปลายทางต้องตอบ HTTP 2xx พร้อม JSON ที่มี `event_id` ตรงกับที่ได้รับ หรือปลายทางถูกหยุดส่งชั่วคราวเพราะล้มเหลวติดกัน |
| ผู้ใช้ทุกคนโดน rate limit พร้อมกัน | IP จริงหายระหว่างทาง: เช็ค subnet ใน `scripts/setup-networks.sh` กับ `set_real_ip_from` ใน `nginx.conf` และ nginx ของเพื่อนต้องใช้ `$proxy_add_x_forwarded_for` |
