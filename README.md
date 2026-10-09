# SmartLPR

ระบบอ่านป้ายทะเบียนรถจากกล้อง RTSP แล้วส่งผล (ทะเบียน จังหวัด สีรถ และรูป) ไปยัง webhook ของลูกค้า

**สถานะ:** พร้อมใช้งานจริง (Production)

## ระบบทำงานอย่างไร

```
กล้อง RTSP ──► camera ──► backend ──► webhook ของลูกค้า
                             ▲  │
ผู้ใช้/พาร์ทเนอร์ ──► frontend ─┘  ▼
                                db
```

ระบบรันด้วย Docker Compose 4 คอนเทนเนอร์:

| คอนเทนเนอร์ | หน้าที่ | พอร์ต |
| --- | --- | --- |
| `smartlpr-db` | ฐานข้อมูล PostgreSQL 15 | ไม่เปิดออกนอกเครื่อง |
| `smartlpr-backend` | API (FastAPI) และงานเบื้องหลังที่ส่ง webhook | 8000 ภายในเท่านั้น |
| `smartlpr-frontend` | nginx แจกหน้าเว็บ และส่งต่อ `/api/` ไป backend | 8081 |
| `smartlpr-camera` | ดึงภาพจากกล้อง ตรวจรถ หาป้าย อ่านตัวอักษร ทายสีรถ | ไม่มี |

## สิ่งที่ต้องมีก่อนติดตั้ง

- Docker และ Docker Compose
- ไฟล์โมเดล AI 4 ไฟล์ (ไม่อยู่ใน git เพราะไฟล์ใหญ่ ต้องขอจากผู้ดูแลโปรเจกต์)
- บัญชี Gmail พร้อมรหัสผ่านแอป สำหรับส่งอีเมล OTP

## ติดตั้งและรัน

1. clone โปรเจกต์

   ```bash
   git clone <URL ของ repository>
   cd SmartLPR
   ```

2. วางไฟล์โมเดลตามโครงสร้างนี้

   ```
   models/
   ├── detector/yolo11n.pt          ตรวจหารถ
   ├── yolo/best.pt                 หากรอบป้ายทะเบียน
   ├── ocr/thai_platev1.pth         อ่านตัวอักษรบนป้าย
   └── color/
       ├── car_color_model.h5       ทายสีรถ
       └── class_names.json
   ```

3. สร้างไฟล์ตั้งค่า แล้วแก้ค่าที่อยู่ในเครื่องหมาย `<...>`

   ```bash
   cp .env.example .env
   ```

4. เริ่มระบบ

   ```bash
   docker compose up -d --build
   ```

5. เปิดหน้าเว็บ (ผ่านโดเมน HTTPS ของระบบ หรือ `http://localhost:8081` บนเครื่องเซิร์ฟเวอร์) สมัครบัญชีแรกและยืนยัน OTP ทางอีเมล

6. ตั้งบัญชีนั้นเป็นผู้ดูแล

   ```bash
   docker compose exec backend python make_admin.py <อีเมล>
   ```

ตารางในฐานข้อมูลถูกสร้างอัตโนมัติเมื่อ backend เริ่มทำงานครั้งแรก

## ข้อควรรู้เมื่อใช้งานจริง

- **HTTPS:** ให้บริการผ่าน reverse proxy ที่ทำ HTTPS แล้วส่งต่อมาที่พอร์ต 8081 และตั้ง `COOKIE_SECURE=true` ใน `.env` (ถ้าเข้าเว็บผ่าน `http://` ต้องตั้งเป็น `false` ไม่เช่นนั้น login จะหลุดทุก 15 นาที)
- **IP จริงของผู้ใช้:** `frontend/nginx.conf` เชื่อ header `X-Forwarded-For` เฉพาะจาก IP ในบรรทัด `set_real_ip_from` (gateway ของ Docker network) ถ้าสร้าง network ใหม่แล้วเลข gateway เปลี่ยน ต้องแก้เลขนี้ตาม ไม่เช่นนั้นผู้ใช้ทุกคนจะถูกนับเป็น IP เดียวกันและโดน rate limit พร้อมกัน
- **ห้ามเปลี่ยน `SECRET_KEY` หลังเปิดใช้งาน:** API key, OTP และ refresh token ถูกเก็บเป็น hash ที่ผูกกับคีย์นี้ ถ้าเปลี่ยน API key ของผู้ใช้ทุกคนจะใช้ไม่ได้และทุกคนต้อง login ใหม่
- **ห้ามใช้ `docker compose down -v`:** ตัวเลือก `-v` ลบ volume `db_data` ซึ่งเป็นฐานข้อมูลทั้งหมด
- **เปลี่ยนไฟล์โมเดล:** คอนเทนเนอร์ camera อ่านโมเดลจาก volume `model_data` ซึ่งถูกคัดลอกจาก `models/` ตอนสร้างครั้งแรกเท่านั้น เมื่อเปลี่ยนไฟล์โมเดลต้องลบ volume นี้แล้ว build ใหม่ (`docker compose down` → `docker volume rm <ชื่อ volume ที่ลงท้ายด้วย model_data>` → `docker compose up -d --build`)
- **โค้ดบนเซิร์ฟเวอร์:** คอนเทนเนอร์ backend อ่านโค้ดจากโฟลเดอร์โปรเจกต์โดยตรงและรีโหลดเองเมื่อไฟล์เปลี่ยน จึงไม่ควรแก้ไฟล์บนเซิร์ฟเวอร์โดยตรง ให้แก้ผ่าน git แล้ว deploy ตามหัวข้อ "การ deploy"

## คำสั่งที่ใช้บ่อย

| งาน | คำสั่ง |
| --- | --- |
| เริ่มระบบ | `docker compose up -d` |
| หยุดระบบ | `docker compose down` |
| ดู log | `docker compose logs -f backend` (หรือ `camera`) |
| ตั้งผู้ดูแล | `docker compose exec backend python make_admin.py <อีเมล>` |
| ถอดสิทธิ์ผู้ดูแล | `docker compose exec backend python make_admin.py <อีเมล> --revoke` |
| สำรองฐานข้อมูล | `docker compose exec -T db pg_dump -U <DB_USER> <DB_NAME> > backup.sql` |
| กู้คืนฐานข้อมูล | `docker compose exec -T db psql -U <DB_USER> <DB_NAME> < backup.sql` |

เอกสาร API แบบ Swagger อยู่ที่ `http://localhost:8081/docs`

## โครงสร้างโปรเจกต์

| ตำแหน่ง | หน้าที่ |
| --- | --- |
| `smartlpr/` | แกนของ backend: จุดเริ่มแอป (`main.py`), ค่าตั้ง (`config.py`), ตารางฐานข้อมูล (`models.py`), การยืนยันตัวตน (`security.py`) |
| `routers/` | ประกาศ endpoint แยกตามกลุ่ม |
| `services/` | ตรรกะการทำงานของแต่ละกลุ่ม รวมถึงอีเมล rate limit และ audit log |
| `worker.py` | งานเบื้องหลัง: ส่ง webhook, ตรวจกล้องใหม่, ลบข้อมูลหมดอายุ |
| `camera/` | จัดการโปรเซสกล้อง ดึงภาพ และรันโมเดล AI |
| `security/` | ตรวจ URL ของ webhook และกล้องไม่ให้ชี้เข้าเครือข่ายภายใน |
| `plate_detection_repo/` | โค้ดโครงข่าย OCR |
| `frontend/` | หน้าเว็บ ค่าตั้ง nginx และคู่มือผู้ใช้ (`user-guide.pdf`) |

## ข้อมูลที่ระบบสร้างตอนรัน

| ตำแหน่ง | เนื้อหา |
| --- | --- |
| `captures/camera_{id}/` | รูปเต็ม (`full`) และรูปป้ายที่ตัดแล้ว (`crop`) ถูกลบเมื่อกล้องตัวนั้นถูกลบออกจากระบบ |
| `logs/` | log ของกล้องแต่ละตัวและของ AI worker |
| volume `db_data` | ไฟล์ฐานข้อมูล |

ทั้งหมดนี้ไม่อยู่ใน git

รายการ event ในฐานข้อมูลที่เก่าเกิน 30 วันจะถูกซ่อนจากระบบอัตโนมัติ (soft delete) แต่ไฟล์รูปใน `captures/` และไฟล์ log ไม่ถูกลบตามอายุ จึงควรตรวจพื้นที่ดิสก์เป็นระยะ

## การ deploy

การ push เข้า branch `main` จะสั่ง GitHub Actions (`.github/workflows/deploy.yml`) ให้ดึงโค้ดล่าสุดและ build คอนเทนเนอร์ใหม่บนเครื่อง VM โดยอัตโนมัติ

ไฟล์ `.env` และโฟลเดอร์ `models/` ไม่ได้มากับ git จึงต้องวางบน VM ด้วยมือ

## แก้ปัญหาเบื้องต้น

| อาการ | สาเหตุที่พบบ่อย |
| --- | --- |
| backend ไม่ขึ้น แจ้งว่า "ไม่พบ Environment Variable" | ยังไม่ได้ตั้งค่าตัวแปรนั้นใน `.env` |
| camera ไม่ขึ้น | path ของโมเดลใน `.env` ไม่ตรง หรือยังไม่ได้วางไฟล์โมเดล |
| ไม่ได้รับอีเมล OTP | `SMTP_USER` หรือ `SMTP_APP_PASSWORD` ไม่ถูกต้อง |
| login หลุดทุก 15 นาที | ตั้ง `COOKIE_SECURE=true` แต่เข้าเว็บผ่าน http |
| เพิ่มกล้องหรือ webhook ในวง LAN ไม่ได้ | `ALLOW_PRIVATE_IP=false` กัน IP ภายในไว้ |
| ลูกค้าไม่ได้รับ webhook | ปลายทางต้องตอบ HTTP 2xx พร้อม JSON ที่มี `event_id` ตรงกับที่ได้รับ |
