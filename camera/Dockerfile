# ใช้ Python 3.11 slim
FROM python:3.11-slim-bookworm

# ตั้งค่า Working Directory
WORKDIR /app

# ติดตั้ง System dependencies ที่จำเป็นสำหรับไลบรารีประมวลผลภาพ (OpenCV, etc.)
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ติดตั้งแพ็กเกจของกล้อง (ทำก่อน COPY โค้ด เพื่อให้ Docker cache ชั้นนี้ไว้ แก้โค้ดแล้วไม่ต้องติดตั้งใหม่)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# คัดลอกโค้ดของ camera (โฟลเดอร์ app/) รวมโมเดลใน app/models เข้ามาใน Container
COPY app/ .

# คำสั่งเริ่มต้นสำหรับรัน Camera Manager
CMD ["python", "camera/camera_manager.py"]
