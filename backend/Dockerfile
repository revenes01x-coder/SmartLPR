# ใช้ Python 3.11 slim เป็นฐานเพื่อให้ขนาดไฟล์ไม่ใหญ่เกินไป
FROM python:3.11-slim-bookworm

# ตั้งค่า Working Directory
WORKDIR /app

# ติดตั้ง System dependencies ที่จำเป็นสำหรับไลบรารีประมวลผลภาพ (OpenCV ใช้ตรวจ RTSP ตอนเพิ่มกล้อง)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ติดตั้งแพ็กเกจ (ทำก่อน COPY โค้ด เพื่อให้ Docker cache ชั้นนี้ไว้)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# คัดลอกโค้ดของ backend (โฟลเดอร์ app/) — ไม่มีโมเดล AI แล้ว image เล็กลงมาก
COPY app/ .

# เปิดพอร์ต 8000
EXPOSE 8000

# คำสั่งเริ่มต้นสำหรับรันเซิร์ฟเวอร์ FastAPI
CMD ["uvicorn", "smartlpr.main:app", "--host", "0.0.0.0", "--port", "8000"]
