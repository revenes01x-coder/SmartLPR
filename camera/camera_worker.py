import os
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"   # ซ่อน log ของ TensorFlow (INFO/WARNING/oneDNN ฯลฯ)
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"  # กัน warning เรื่อง oneDNN round-off เฉยๆ
os.environ["YOLO_VERBOSE"] = "False"       # ซ่อน banner/log ของ Ultralytics ตอนโหลดโมเดล
# บังคับใช้ TCP ลดปัญหา packet drop + ปิด Buffer ในระดับ FFmpeg (Zero-Buffer) + โหมด Low Delay
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"

import re
import time
import uuid
import json
import queue
import logging
import difflib
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2 as cv
import numpy as np
import requests
from ultralytics import YOLO
from camera.plate_ocr import predict as ocr_predict
from security.camera_url_guard import resolve_rtsp_url_pinned
from security.ip_guard import SSRFBlockedError

from smartlpr.config import (
    PLATE_YOLO_MODEL_PATH,
    CAR_DETECTOR_MODEL_PATH,
    CAR_COLOR_MODEL_PATH,
    CAR_COLOR_CLASSNAMES_PATH,
    CAPTURES_SAVE_DIR,
    CAPTURE_EVENT_WEBHOOK_URL,
    CAPTURE_EVENT_SECRET,   # [Internal Auth] secret กลาง ยิงคู่กับ backend ผ่าน header
    CAMERA_DETECTION_FPS,
)

import tensorflow as tf
tf.get_logger().setLevel("ERROR")  # ซ่อน log ระดับ absl ที่ TF_CPP_MIN_LOG_LEVEL เก็บไม่หมด
from tensorflow.keras.applications.efficientnet import preprocess_input as color_model_preprocess

_missing = [
    name for name, value in (
        ("PLATE_YOLO_MODEL_PATH", PLATE_YOLO_MODEL_PATH),
        ("CAR_COLOR_MODEL_PATH", CAR_COLOR_MODEL_PATH),
        ("CAR_COLOR_CLASSNAMES_PATH", CAR_COLOR_CLASSNAMES_PATH),
    ) if not value
]
if _missing:
    raise RuntimeError(
        "camera/camera_worker.py ต้องการ Environment Variable ต่อไปนี้ใน .env: "
        f"{', '.join(_missing)} (ดูตัวอย่างค่าที่ต้องตั้งใน .env.example)"
    )

YOLO_MODEL_PATH = PLATE_YOLO_MODEL_PATH                 # YOLO หาป้ายทะเบียน (เทรนเอง)
CAR_CLASS_IDS = [2, 3, 5, 7]  # COCO class id: 2=car, 3=motorcycle, 5=bus, 7=truck
CAR_BOX_EXPAND_RATIO = 0.025  # ขยายกรอบรถออกก่อนหาป้าย กันป้ายโดนตัดขาดถ้าอยู่ขอบกรอบพอดี

COLOR_MODEL_PATH = CAR_COLOR_MODEL_PATH
COLOR_CLASSNAMES_PATH = CAR_COLOR_CLASSNAMES_PATH
COLOR_IMG_SIZE = (224, 224)  # ต้องตรงกับตอนเทรน (IMG_SIZE ใน train_car_color.py)
COLOR_MIN_CONFIDENCE = 0.3   # ถ้าโมเดลมั่นใจต่ำกว่านี้ ให้ตอบ "unknown" แทน

SAVE_DIR_ROOT   = CAPTURES_SAVE_DIR                     # ดีฟอลต์ "captures" (relative, อยู่ใน .gitignore)
WEBHOOK_URL     = CAPTURE_EVENT_WEBHOOK_URL             # ดีฟอลต์ "http://localhost:8000/capture-event"

YOLO_CONF        = 0.50
MIN_ASPECT_RATIO = 0.8
MIN_WIDTH        = 50
RESIZE_FACTOR    = 3
PADDING          = 15
RECONNECT_SEC    = 10   # วินาทีที่รอก่อน reconnect กล้อง
OCR_MIN_CONFIDENCE = 0.9
DETECTION_FPS    = float(CAMERA_DETECTION_FPS or 5.0)  # จำกัดความถี่การรัน AI ตรวจจับ (ดีฟอลต์ 5 FPS)
# ============================================================

THAI_PROVINCES = [
    "กรุงเทพมหานคร", "กระบี่", "กาญจนบุรี", "กาฬสินธุ์", "กำแพงเพชร", "ขอนแก่น", "จันทบุรี",
    "ฉะเชิงเทรา", "ชลบุรี", "ชัยนาท", "ชัยภูมิ", "ชุมพร", "เชียงราย", "เชียงใหม่", "ตรัง",
    "ตราด", "ตาก", "นครนายก", "นครปฐม", "นครพนม", "นครราชสีมา", "นครศรีธรรมราช",
    "นครสวรรค์", "นนทบุรี", "นราธิวาส", "น่าน", "บึงกาฬ", "บุรีรัมย์", "ปทุมธานี",
    "ประจวบคีรีขันธ์", "ปราจีนบุรี", "ปัตตานี", "พระนครศรีอยุธยา", "พะเยา", "พังงา",
    "พัทลุง", "พิจิตร", "พิษณุโลก", "เพชรบุรี", "เพชรบูรณ์", "แพร่", "ภูเก็ต",
    "มหาสารคาม", "มุกดาหาร", "แม่ฮ่องสอน", "ยโสธร", "ยะลา", "ร้อยเอ็ด", "ระนอง",
    "ระยอง", "ราชบุรี", "ลพบุรี", "ลำปาง", "ลำพูน", "เลย", "ศรีสะเกษ", "สกลนคร",
    "สงขลา", "สตูล", "สมุทรปราการ", "สมุทรสงคราม", "สมุทรสาคร", "สระแก้ว", "สระบุรี",
    "สิงห์บุรี", "สุโขทัย", "สุพรรณบุรี", "สุราษฎร์ธานี", "สุรินทร์", "หนองคาย",
    "หนองบัวลำภู", "อ่างทอง", "อำนาจเจริญ", "อุดรธานี", "อุตรดิตถ์", "อุทัยธานี", "อุบลราชธานี", "เบตง"
]


def detect_car_color(color_model, class_names, car_img, logger, min_confidence=COLOR_MIN_CONFIDENCE):
    """ทายสีรถจากภาพที่ครอปมาแล้ว คืนชื่อสี (capitalize) หรือ 'unknown'"""
    if car_img is None or car_img.size == 0:
        return "unknown"

    try:
        img = cv.resize(car_img, COLOR_IMG_SIZE)
        img = cv.cvtColor(img, cv.COLOR_BGR2RGB)  # ตอนเทรนใช้ ImageDataGenerator ซึ่งอ่านภาพเป็น RGB

        batch = np.expand_dims(img.astype(np.float32), axis=0)
        batch = color_model_preprocess(batch)

        preds = color_model.predict(batch, verbose=0)[0]  # softmax probs ต่อ class
        best_idx = int(np.argmax(preds))
        best_color = class_names[best_idx]
        best_confidence = float(preds[best_idx])

        if best_confidence < min_confidence:
            return "unknown"
        return best_color.capitalize()
    except Exception as e:
        logger.warning(f"ทายสีรถผิดพลาด: {e}")
        return "unknown"


def fixformat(text):
    text = text.strip()
    match = re.match(r'^([0-9A-Za-z]?[\u0E00-\u0E7F]+)(.*)$', text)
    if match:
        char_part = match.group(1).strip()
        num_part = match.group(2).strip()
    else:
        if any('\u0E00' <= c <= '\u0E7F' for c in text):
            char_part, num_part = text.strip(), ""
        else:
            char_part, num_part = "", text.strip()

    is_standard = re.fullmatch(r'^[0-9lIoOSzZ]?[ก-ฮ]{1,2}$', char_part)
    if is_standard and len(char_part) > 1 and char_part[0] in '|lIoOSzZ':
        prefix_fixes = {'l': '1', 'I': '1', 'o': '0', 'O': '0', 'S': '5', 'z': '2', 'Z': '2'}
        char_part = prefix_fixes.get(char_part[0], char_part[0]) + char_part[1:]

    num_fixes = {'|': '1', 'o': '0', 'O': '0', 'l': '1', 'I': '1', 'S': '5', 's': '5',
                 'G': '6', 'B': '8', 'Z': '2', 'z': '2', 'A': '4', 'q': '9'}
    fixed_num = ''.join([num_fixes.get(c, c) for c in num_part])

    # ตัดตัวอักษรแปลกปลอม/ขยะที่อาจหลุดมาอยู่หลังหรือในส่วนตัวเลข (เช่น '8603 ฉ' -> '8603')
    if char_part:
        # สำหรับป้ายที่มีหมวดอักษร: ตัวเลขทะเบียนต้องเป็นตัวเลข 1-4 หลักเท่านั้น
        digit_match = re.search(r'\d{1,4}', fixed_num)
        if digit_match:
            fixed_num = digit_match.group(0)
        else:
            fixed_num = re.sub(r'\D', '', fixed_num)[:4]
    else:
        # กรณีป้ายตัวเลขล้วน (เช่น รถบรรทุก/รถโดยสาร 70-1234)
        fixed_num = re.sub(r'\D', '', fixed_num)[:6]

    return char_part + fixed_num


def find_province_at_end(text: str) -> tuple[str, int]:
    """ค้นหาชื่อจังหวัดที่อยู่ท้ายข้อความ (รองรับทั้งกรณีมี/ไม่มีเว้นวรรค และกรณีตัวอักษรเพี้ยน เช่น กรุ3เทพ)
    คืนค่า (ชื่อจังหวัด, ตำแหน่ง index ที่เริ่มต้นชื่อจังหวัดใน text) หากไม่พบคืน ('unknown', -1)"""
    if len(text) < 3:
        return "unknown", -1

    text_clean = text.replace('3', 'ง').replace('0', 'อ').replace('1', 'เ')
    best_prov = None
    best_idx = -1
    best_score = 0.0

    # 1. Exact match ค้นหาชื่อจังหวัดที่ยาวที่สุดก่อน (กันจังหวัดสั้นทับชื่อจังหวัดยาว)
    for prov in sorted(THAI_PROVINCES, key=len, reverse=True):
        idx = text.rfind(prov)
        if idx != -1:
            return prov, idx
        idx_c = text_clean.rfind(prov)
        if idx_c != -1:
            return prov, idx_c

    # 2. Fuzzy match จากท้ายข้อความ (ความยาว 3 - 16 ตัวอักษร)
    for prov in THAI_PROVINCES:
        plen = len(prov)
        for cand_len in range(max(3, plen - 2), min(len(text), plen + 3)):
            tail = text[-cand_len:]
            tail_c = text_clean[-cand_len:]
            score = max(
                difflib.SequenceMatcher(None, tail, prov).ratio(),
                difflib.SequenceMatcher(None, tail_c, prov).ratio(),
            )
            if score >= 0.65 and score > best_score:
                best_score = score
                best_prov = prov
                best_idx = len(text) - cand_len

    if best_prov and best_score >= 0.65:
        return best_prov, best_idx

    return "unknown", -1


def preprocess_plate(img, x1, y1, x2, y2):
    h, w = img.shape[:2]
    y1, y2 = max(0, y1 - PADDING), min(h, y2 + PADDING)
    x1, x2 = max(0, x1 - PADDING), min(w, x2 + PADDING)
    plate_crop = img[y1:y2, x1:x2]
    if plate_crop.size == 0:
        return img, img
    h_new = plate_crop.shape[0] * RESIZE_FACTOR
    w_new = plate_crop.shape[1] * RESIZE_FACTOR
    plate_crop = cv.resize(plate_crop, (w_new, h_new), interpolation=cv.INTER_CUBIC)
    gray = cv.cvtColor(plate_crop, cv.COLOR_BGR2GRAY)
    clahe = cv.createCLAHE(clipLimit=1.0, tileGridSize=(8, 8))
    enhanced_gray = clahe.apply(gray)
    return plate_crop, enhanced_gray


def read_plate(plate_crop, enhanced_gray, logger):
    """อ่านป้ายทะเบียน ลองภาพที่ enhance (CLAHE) ก่อน
    ถ้าผลลัพธ์รอบแรกมั่นใจสูง (>= 0.85) จะ Early Exit ทันทีเพื่อประหยัดเวลา CPU
    ถ้ายังไม่มั่นใจ จึงค่อยลองภาพ crop สีปกติ (fallback) มาเทียบ

    ถ้าผลลัพธ์ที่มั่นใจที่สุดยังต่ำกว่า OCR_MIN_CONFIDENCE (หรืออ่านได้ค่าว่าง) ถือว่าอ่านไม่ได้
    -> คืนค่าว่าง ("", "") ไม่ส่งข้อมูลที่ไม่น่าเชื่อถือออกไปยิง webhook"""
    text, confidence = ocr_predict(enhanced_gray)
    source = "enhanced_gray"

    # Early Exit: ถ้าภาพ enhance มั่นใจสูงแล้ว ไม่ต้องเสียเวลารันรอบ 2 ซ้ำ
    if not text or confidence < 0.85:
        fallback_text, fallback_confidence = ocr_predict(plate_crop)
        if fallback_text and fallback_confidence > confidence:
            text, confidence, source = fallback_text, fallback_confidence, "plate_crop"

    if not text or confidence < OCR_MIN_CONFIDENCE:
        logger.info(
            f"ข้ามป้าย: อ่านได้ '{text}' มั่นใจ {confidence:.2f} ต่ำกว่าเกณฑ์ "
            f"{OCR_MIN_CONFIDENCE} (แหล่งที่มั่นใจสุด: {source})"
        )
        return "", "", 0.0

    text = text.replace('.', '').replace(',', '').replace('-', '').strip()

    # แยกเลขทะเบียนและจังหวัดออกจากกันอย่างแม่นยำ (รองรับทั้งป้ายปกติ ป้ายประมูล และป้ายพิเศษ)
    province, prov_idx = find_province_at_end(text)
    if prov_idx != -1:
        raw_plate = text[:prov_idx].strip()
        province_part = province
    else:
        if ' ' in text:
            parts = text.split(' ', 1)
            raw_plate = parts[0].strip()
        else:
            raw_plate = text
        province_part = "unknown"

    plate_part = fixformat(raw_plate)
    return plate_part, province_part, confidence


def _setup_logger(camera_id: str) -> logging.Logger:
    os.makedirs("logs", exist_ok=True)
    logger = logging.getLogger(f"camera_{camera_id}")
    logger.setLevel(logging.INFO)
    # กัน handler ซ้ำถ้า process ถูก restart แล้วเรียก run() ใหม่ในตัวเดิม (ปกติไม่เกิดเพราะเป็น process ใหม่ทุกครั้ง)
    if not logger.handlers:
        handler = logging.FileHandler(f"logs/camera_{camera_id}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    return logger


def send_to_webhook(camera_id, path_full, path_crop, plate, province, color, ts_display, logger, event_id=None, is_update=False):
    try:
        data = {
            "camera_id": camera_id,
            "event_id": event_id or uuid.uuid4().hex,  # ใช้ event_id เดิมกรณีอัปเกรด
            "plate": plate,
            "province": province,
            "color": color,
            "timestamp": ts_display,
            "full_image_path": path_full,
            "crop_image_path": path_crop,
            "is_update": str(is_update).lower(),
        }

        # [Internal Auth]: แนบ secret กลางไปด้วยทุกครั้ง — backend (/capture-event)
        # จะปฏิเสธ request ที่ไม่มี header นี้หรือค่าไม่ตรง (ดู smartlpr/security.py:
        # require_capture_event_secret) กันคนนอกที่รู้ camera_id ยิงข้อมูล/path ปลอมเข้ามา
        response = requests.post(
            WEBHOOK_URL,
            data=data,
            headers={"X-Capture-Secret": CAPTURE_EVENT_SECRET},
            timeout=5,
        )

        if response.status_code == 200:
            action_desc = "อัปเดต webhook" if is_update else "ส่ง webhook"
            logger.info(f"{action_desc} สำเร็จ")
        elif response.status_code == 401:
            logger.error("webhook ปฏิเสธ (401) — CAPTURE_EVENT_SECRET ไม่ตรงกับฝั่ง backend เช็ค .env ทั้งสองฝั่ง")
        else:
            logger.warning(f"webhook ตอบกลับ: {response.status_code}")

    except requests.exceptions.ConnectionError:
        logger.warning("เชื่อมต่อ webhook ไม่ได้ — เซฟเฉพาะไฟล์ local")
    except requests.exceptions.Timeout:
        logger.warning("webhook หมดเวลา — เซฟเฉพาะไฟล์ local")
    except Exception as e:
        logger.error(f"ส่ง webhook ผิดพลาด: {e}")


def _save_and_send_worker(camera_id, frame, plate_crop, plate, province, color, save_dir_full, save_dir_crop, logger, event_id=None, is_update=False):
    try:
        now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=7)
        ts_file = now.strftime("%Y%m%d_%H%M%S")
        ts_display = now.strftime("%Y/%m/%d %H:%M:%S")

        prov_str = f"_{province}" if province and province != "unknown" else ""

        fname_full = f"{ts_file}_{plate}{prov_str}_full.jpg"
        path_full = os.path.join(save_dir_full, fname_full)
        cv.imwrite(path_full, frame)

        fname_crop = f"{ts_file}_{plate}{prov_str}_crop.jpg"
        path_crop = os.path.join(save_dir_crop, fname_crop)
        cv.imwrite(path_crop, plate_crop)

        logger.info(f"เซฟรูป full: {path_full}")
        logger.info(f"เซฟรูป crop: {path_crop}")

        send_to_webhook(camera_id, path_full, path_crop, plate, province, color, ts_display, logger, event_id=event_id, is_update=is_update)
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดในการเซฟรูป/ส่ง webhook: {e}")


def save_capture(camera_id, frame, plate_crop, plate, province, color, save_dir_full, save_dir_crop, logger, io_executor=None, event_id=None, is_update=False):
    frame_copy = frame.copy()
    crop_copy = plate_crop.copy()
    if io_executor is not None:
        io_executor.submit(
            _save_and_send_worker,
            camera_id, frame_copy, crop_copy, plate, province, color,
            save_dir_full, save_dir_crop, logger,
            event_id, is_update,
        )
    else:
        _save_and_send_worker(
            camera_id, frame_copy, crop_copy, plate, province, color,
            save_dir_full, save_dir_crop, logger,
            event_id, is_update,
        )


def open_stream(url, logger):
    """เปิด RTSP stream ด้วย IP ที่ resolve + เช็คแล้วเท่านั้น (pin IP กัน DNS rebinding)

    เหตุผลที่ต้อง "แทน IP ตรงๆ ใน URL" ก่อนส่งให้ cv.VideoCapture แทนที่จะแค่เช็คแล้วปล่อยผ่าน
    hostname เดิม: cv.VideoCapture เปิด RTSP ผ่าน FFmpeg (C library) ซึ่ง resolve DNS เองอีกรอบ
    ไม่ผ่าน Python เลย ต่อให้ฝั่ง Python เช็คแล้วว่า IP ปลอดภัย ก็ไม่การันตีว่า FFmpeg จะได้ IP
    เดียวกัน (ดู camera_url_guard.resolve_rtsp_url_pinned สำหรับรายละเอียดเต็ม)

    คืน None ถ้า host ไม่ผ่านการตรวจสอบ (SSRF) หรือ resolve ไม่ได้ — caller (_open_stream_with_retry)
    จะรอแล้ว retry เอง เหมือนเวลา stream ต่อไม่ติดด้วยเหตุผลอื่น ไม่ crash process ทิ้ง"""
    try:
        pinned_url = resolve_rtsp_url_pinned(url)
    except SSRFBlockedError as e:
        logger.warning(f"[SSRF Guard] ปฏิเสธการเชื่อมต่อ RTSP: {e}")
        return None

    cap = cv.VideoCapture(pinned_url)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
    return cap


def _open_stream_with_retry(rtsp_url, logger):
    """เรียก open_stream() วนซ้ำจนกว่าจะสำเร็จ (ไม่ปล่อย None ออกไปให้ caller เห็นเลย)
    ใช้ทั้งตอนเริ่ม process ครั้งแรกและตอน reconnect หลัง stream หลุด — ถ้าถูก SSRF guard ปฏิเสธ
    (เช่น rtsp_url โดน DNS rebinding ไปชี้ IP ภายในแล้ว) จะวนรอเหมือนกรณี stream ต่อไม่ติดปกติ
    ไม่ crash หรือหยุดทำงานไปเฉยๆ"""
    cap = open_stream(rtsp_url, logger)
    while cap is None:
        logger.warning(f"เชื่อมต่อ RTSP ไม่ได้ (URL ไม่ผ่าน SSRF guard หรือต่อไม่ติด) — รอ {RECONNECT_SEC} วินาทีแล้วลองใหม่...")
        time.sleep(RECONNECT_SEC)
        cap = open_stream(rtsp_url, logger)
    return cap


class RealtimeVideoStream:
    """
    RTSP Stream Reader แบบ Real-time (Zero-Latency)
    แยก Capture Thread ดึงภาพ cap.read() อย่างต่อเนื่องเพื่อระบาย Buffer ของ FFmpeg และ OS Socket ทิ้งตลอดเวลา
    และเก็บเฉพาะภาพเฟรมล่าสุด (Latest Frame) ไว้ให้ AI ประมวลผล ทำให้ไม่มีปัญหาดีเลย์สะสม
    """
    def __init__(self, rtsp_url: str, logger: logging.Logger):
        self.rtsp_url = rtsp_url
        self.logger = logger
        self.cap = None
        self.frame = None
        self.ret = False
        self.running = False
        self.lock = threading.Lock()
        self.thread = None
        self._start()

    def _start(self):
        self.cap = _open_stream_with_retry(self.rtsp_url, self.logger)
        ret, frame = self.cap.read()
        if ret and frame is not None:
            self.frame = frame
            self.ret = True
        self.running = True
        self.thread = threading.Thread(target=self._capture_worker, daemon=True)
        self.thread.start()

    def _capture_worker(self):
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                with self.lock:
                    self.ret = False
                if self.cap:
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None

                self.logger.warning(f"RTSP หลุดการเชื่อมต่อ — รอ {RECONNECT_SEC} วินาทีแล้ว reconnect...")
                time.sleep(RECONNECT_SEC)
                if not self.running:
                    break
                self.cap = open_stream(self.rtsp_url, self.logger)
                if self.cap and self.cap.isOpened():
                    self.logger.info("เชื่อมต่อ RTSP สำเร็จ กำลังรับภาพต่อ...")
                continue

            ret, frame = self.cap.read()
            if not ret or frame is None:
                with self.lock:
                    self.ret = False
                self.logger.warning(f"Stream หลุด (อ่านเฟรมไม่สำเร็จ) — รอ {RECONNECT_SEC} วินาทีแล้ว reconnect...")
                if self.cap:
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                time.sleep(RECONNECT_SEC)
                if not self.running:
                    break
                self.cap = open_stream(self.rtsp_url, self.logger)
                if self.cap and self.cap.isOpened():
                    self.logger.info("เชื่อมต่อ RTSP สำเร็จ กำลังรับภาพต่อ...")
                continue

            with self.lock:
                self.frame = frame
                self.ret = True

    def read(self):
        """คืนค่า (ret, frame) ของภาพสดใหม่ล่าสุด (Thread-safe)"""
        with self.lock:
            if not self.ret or self.frame is None:
                return False, None
            return True, self.frame.copy()

    def release(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None


def run(camera_id: str, rtsp_url: str, delay: int = 1):
    """
    Entry point ที่ camera_manager.py เรียกผ่าน
    multiprocessing.Process(target=run, args=(camera_id, rtsp_url, delay))
    ฟังก์ชันนี้ loop ไม่มีวันจบ (จบก็ต่อเมื่อ process ถูก terminate จาก manager)
    """
    logger = _setup_logger(camera_id)

    save_dir_full = os.path.join(SAVE_DIR_ROOT, f"camera_{camera_id}", "full")
    save_dir_crop = os.path.join(SAVE_DIR_ROOT, f"camera_{camera_id}", "crop")
    os.makedirs(save_dir_full, exist_ok=True)
    os.makedirs(save_dir_crop, exist_ok=True)

    logger.info("กำลังโหลด YOLO model (ป้ายทะเบียน)...")
    yolo_model = YOLO(YOLO_MODEL_PATH)

    logger.info("กำลังโหลด YOLO model (ตรวจจับรถทั้งคัน)...")
    car_detector = YOLO(CAR_DETECTOR_MODEL_PATH)

    logger.info("กำลังโหลดโมเดลแยกสีรถ...")
    color_model = tf.keras.models.load_model(COLOR_MODEL_PATH)
    with open(COLOR_CLASSNAMES_PATH, "r", encoding="utf-8") as f:
        color_class_names = json.load(f)

    logger.info(f"เชื่อมต่อ RTSP: {rtsp_url}")
    stream = RealtimeVideoStream(rtsp_url, logger)
    io_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"cam_io_{camera_id}")

    recent_plates: dict[str, float] = {}  # {plate: เวลา (time.time()) ล่าสุดที่เจอป้ายนี้} — กันตรวจจับซ้ำสำหรับทะเบียนเดิมตาม delay
    last_cleanup_time = time.time()
    last_detection_time = 0.0
    detection_interval = 1.0 / max(0.5, float(DETECTION_FPS))

    logger.info(
        f"เริ่มทำงานแบบ Real-time (กล้อง: {camera_id}, delay ทะเบียนเดิม: {delay} วินาที, "
        f"detection: {DETECTION_FPS} FPS)"
    )

    try:
        while True:
            ret, frame = stream.read()

            if not ret or frame is None:
                time.sleep(0.05)
                continue

            now = time.time()

            # [Frame Throttling]: ตรวจสอบว่าถึงรอบการรัน AI ตรวจจับหรือยัง (จำกัดตาม DETECTION_FPS)
            # หากยังไม่ถึงรอบ ให้พักสั้นๆ เพื่อไม่ให้ CPU หมุนฟรี แล้ววนกลับไปอ่านเฟรมสดถัดไป
            if (now - last_detection_time) < detection_interval:
                sleep_time = max(0.005, min(0.03, detection_interval - (now - last_detection_time)))
                time.sleep(sleep_time)
                continue

            last_detection_time = now
            h_frame, w_frame = frame.shape[:2]

            # ขั้นที่ 1: หา "รถ/มอเตอร์ไซค์" ทั้งเฟรมก่อน ด้วย YOLO pretrained (COCO)
            car_results = car_detector(frame, verbose=False)

            for car_result in car_results:
                for car_box_raw in car_result.boxes:
                    cls_id = int(car_box_raw.cls[0])
                    if cls_id not in CAR_CLASS_IDS:
                        continue

                    cx1, cy1, cx2, cy2 = map(int, car_box_raw.xyxy[0])
                    car_w, car_h = cx2 - cx1, cy2 - cy1

                    # ขยายกรอบรถแบบสัดส่วน ก่อนไปหาป้าย กันป้ายโดนตัดขาดถ้าอยู่ขอบกรอบพอดี
                    pad_x = int(car_w * CAR_BOX_EXPAND_RATIO)
                    pad_y = int(car_h * CAR_BOX_EXPAND_RATIO)
                    ex1 = max(0, cx1 - pad_x)
                    ey1 = max(0, cy1 - pad_y)
                    ex2 = min(w_frame, cx2 + pad_x)
                    ey2 = min(h_frame, cy2 + pad_y)

                    car_crop = frame[ey1:ey2, ex1:ex2]
                    if car_crop.size == 0:
                        continue

                    # ขั้นที่ 2: หาป้ายทะเบียน "เฉพาะในกรอบรถ" ด้วย YOLO ที่เทรนเอง
                    # ตัดปัญหาไปจับป้ายอื่นที่ไม่ใช่ของรถคันนี้
                    plate_results = yolo_model(car_crop, verbose=False)

                    valid_plates = []
                    for plate_result in plate_results:
                        for pbox in plate_result.boxes:
                            px1, py1, px2, py2 = map(int, pbox.xyxy[0])
                            conf = pbox.conf[0].item()
                            width, height = px2 - px1, py2 - py1
                            aspect = width / height if height > 0 else 0

                            if aspect < MIN_ASPECT_RATIO or width < MIN_WIDTH:
                                continue
                            if conf <= YOLO_CONF:
                                continue

                            valid_plates.append((px1, py1, px2, py2, conf))

                    if not valid_plates:
                        continue  # รถคันนี้ไม่เจอป้าย ข้ามไปเลย ไม่เสียเวลาทายสี

                    # ขั้นที่ 3: ทายสีรถ — ทำเฉพาะตอนเจอป้ายแล้วเท่านั้น (ประหยัดเวลา)
                    color = detect_car_color(color_model, color_class_names, car_crop, logger)

                    for px1, py1, px2, py2, plate_conf in valid_plates:
                        plate_crop, enhanced_gray = preprocess_plate(car_crop, px1, py1, px2, py2)
                        plate, province, ocr_conf = read_plate(plate_crop, enhanced_gray, logger)

                        if plate:
                            # [กันจับซ้ำทะเบียนเดิม]: อิงตามเวลา delay ที่ user ตั้งมา (วินาที)
                            last_seen = recent_plates.get(plate)
                            if last_seen is not None and (now - last_seen) < delay:
                                logger.info(
                                    f"ข้ามป้าย {plate} — ซ้ำกับที่เพิ่งบันทึกไป "
                                    f"{now - last_seen:.1f} วิ ก่อนหน้า (ยังไม่ครบ delay {delay} วิ)"
                                )
                                continue

                            recent_plates[plate] = now
                            logger.info(
                                f"เจอป้าย: {plate} {province} | สี: {color} | "
                                f"ความมั่นใจ: OCR {ocr_conf * 100:.1f}%, ตรวจจับป้าย {plate_conf * 100:.1f}%"
                            )
                            save_capture(
                                camera_id, frame, plate_crop, plate, province, color,
                                save_dir_full, save_dir_crop, logger, io_executor=io_executor,
                            )

            # [ล้างแคชป้ายเก่า]: ล้าง entry ที่พ้นระยะ delay ไปแล้วทุกๆ 60 วินาที เพื่อไม่ให้ recent_plates โตขึ้นเรื่อยๆ
            if (now - last_cleanup_time) > 60:
                if recent_plates:
                    recent_plates = {
                        p: t for p, t in recent_plates.items()
                        if now - t < delay
                    }
                last_cleanup_time = now
    finally:
        stream.release()
        io_executor.shutdown(wait=False)


# --- Best-Shot Selection & Fuzzy Matching Config ---
FUZZY_SIMILARITY_THRESHOLD = 0.70   # ความคล้ายคลึงของป้ายทะเบียนขั้นต่ำ (70%)
BEST_SHOT_WINDOW_SECONDS = 2.5      # หน้าต่างสะสมภาพในตะกร้า 2.5 วินาที


def calculate_plate_similarity(p1: str, p2: str) -> float:
    """คำนวณความคล้ายคลึงของป้ายทะเบียน 2 ป้าย (0.0 - 1.0)"""
    if not p1 or not p2:
        return 0.0
    if p1 == p2:
        return 1.0
    return difflib.SequenceMatcher(None, p1, p2).ratio()


def is_same_plate(p1: str, p2: str, threshold: float = FUZZY_SIMILARITY_THRESHOLD) -> bool:
    return calculate_plate_similarity(p1, p2) >= threshold


def check_recent_plate(cam_recent: dict[str, dict], plate: str, now: float, delay: float) -> tuple[str, str, float, dict]:
    """
    ตรวจสอบสถานะของป้ายเทียบกับประวัติใน Cooldown:
    คืนค่า (status, matched_plate, time_diff, entry)
    status:
      - 'exact': ป้ายเดิมเป๊ะ 100% ภายใน delay
      - 'similar': ป้ายคล้ายกัน (>= 80%) ภายใน delay (ผู้สมัครสำหรับ Upgrade)
      - 'none': ป้ายใหม่ ไม่ตรงกับใคร
    """
    for rec_plate, entry in list(cam_recent.items()):
        rec_time = entry["timestamp"] if isinstance(entry, dict) else entry
        time_diff = now - rec_time
        if time_diff < delay:
            if plate == rec_plate:
                return "exact", rec_plate, time_diff, entry if isinstance(entry, dict) else {}
            if is_same_plate(plate, rec_plate, FUZZY_SIMILARITY_THRESHOLD):
                return "similar", rec_plate, time_diff, entry if isinstance(entry, dict) else {}
    return "none", "", 0.0, {}


class PlateCluster:
    """ตะกร้าสะสมเฟรมของรถคันเดียวกันที่กำลังแล่นผ่านมุมกล้อง เพื่อคัดเลือกเฟรมที่มั่นใจสูงสุด"""
    def __init__(self, camera_id: str, candidate: dict, now: float):
        self.camera_id = camera_id
        self.start_time = now
        self.last_seen = now
        self.candidates = [candidate]
        self.event_id = uuid.uuid4().hex  # รหัสเหตุการณ์เฉพาะของรถคันนี้

    def matches(self, plate: str) -> bool:
        for c in self.candidates:
            if is_same_plate(c["plate"], plate, FUZZY_SIMILARITY_THRESHOLD):
                return True
        return False

    def add_candidate(self, candidate: dict, now: float):
        self.candidates.append(candidate)
        self.last_seen = now


def finalize_cluster(
    cluster: PlateCluster,
    save_dir_full: str,
    save_dir_crop: str,
    logger: logging.Logger,
    io_executor,
    cam_recent: dict[str, dict],
    delay: float,
):
    """เลือกเฟรมที่มีความมั่นใจสูงสุด (Max Confidence) จากในตะกร้า และส่ง Webhook เพียง 1 ครั้ง"""
    if not cluster.candidates:
        return

    # คัดเลือกเฟรมที่ดีที่สุด โดยเรียงตาม OCR Confidence เป็นหลัก ตามด้วยคะแนนรวม
    best_candidate = max(
        cluster.candidates,
        key=lambda c: (c["ocr_conf"], c["combined_score"])
    )

    best_plate = best_candidate["plate"]
    best_province = best_candidate["province"]
    best_color = best_candidate["color"]

    now = time.time()
    event_id = getattr(cluster, "event_id", uuid.uuid4().hex)

    # บันทึกเข้า cooldown cache พร้อมข้อมูลสำหรับใช้ประเมิน Confidence Upgrade
    cam_recent[best_plate] = {
        "timestamp": now,
        "event_id": event_id,
        "plate": best_plate,
        "province": best_province,
        "color": best_color,
        "ocr_conf": best_candidate["ocr_conf"],
        "plate_conf": best_candidate["plate_conf"],
        "combined_score": best_candidate["combined_score"],
        "frame_count": len(cluster.candidates),
    }

    logger.info(
        f"[กล้อง {cluster.camera_id}] [Best-Shot Basket] จาก {len(cluster.candidates)} เฟรม "
        f"(สะสม {now - cluster.start_time:.2f}s) "
        f"เลือกป้าย: {best_plate} {best_province} | สี: {best_color} | "
        f"ความมั่นใจ: OCR {best_candidate['ocr_conf'] * 100:.1f}%, ตรวจจับป้าย {best_candidate['plate_conf'] * 100:.1f}%"
    )

    # บันทึกรูปและส่ง Webhook ครั้งแรกเพียง 1 ครั้ง
    save_capture(
        cluster.camera_id,
        best_candidate["frame"],
        best_candidate["plate_crop"],
        best_plate,
        best_province,
        best_color,
        save_dir_full,
        save_dir_crop,
        logger,
        io_executor=io_executor,
        event_id=event_id,
        is_update=False,
    )


def run_inference_worker(frame_queue, stop_event=None):
    """
    Centralized Inference Worker:
    โหลดโมเดล AI ทั้ง 4 ตัวไว้ใน RAM/VRAM เพียงชุดเดียว
    และคอยดึงงาน (camera_id, frame, timestamp, delay) จาก frame_queue มาประมวลผล
    พร้อมระบบ Best-Shot Selection & Multi-frame Voting (Fuzzy 80%)
    """
    os.makedirs("logs", exist_ok=True)
    logger = logging.getLogger("inference_worker")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler("logs/inference_worker.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    logger.info("กำลังโหลด AI models สำหรับ Central Inference Worker...")
    logger.info("กำลังโหลด YOLO model (ป้ายทะเบียน)...")
    yolo_model = YOLO(YOLO_MODEL_PATH)

    logger.info("กำลังโหลด YOLO model (ตรวจจับรถทั้งคัน)...")
    car_detector = YOLO(CAR_DETECTOR_MODEL_PATH)

    logger.info("กำลังโหลดโมเดลแยกสีรถ...")
    color_model = tf.keras.models.load_model(COLOR_MODEL_PATH)
    with open(COLOR_CLASSNAMES_PATH, "r", encoding="utf-8") as f:
        color_class_names = json.load(f)

    logger.info("โหลดโมเดลทั้งหมดเรียบร้อยแล้ว พร้อมเริ่มประมวลผลคิวกลาง...")

    # ThreadPool สำหรับ I/O (บันทึกรูป + ยิง webhook) ไม่ให้บล็อกลูป AI
    io_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="inference_io")

    # {camera_id: {plate: last_seen_time}} — แยกประวัติทะเบียนซ้ำตามกล้อง
    recent_plates_by_cam: dict[str, dict[str, float]] = {}
    active_clusters_by_cam: dict[str, list[PlateCluster]] = {}
    delay_by_cam: dict[str, float] = {}
    last_cleanup_time = time.time()

    QUEUE_TIMEOUT_SENTINEL = object()

    try:
        while True:
            if stop_event and stop_event.is_set():
                break

            try:
                item = frame_queue.get(timeout=0.1)
            except queue.Empty:
                item = QUEUE_TIMEOUT_SENTINEL

            if item is None:  # Sentinel จาก camera_manager สั่งหยุด
                break

            now = time.time()

            if item is not QUEUE_TIMEOUT_SENTINEL:
                camera_id, frame, frame_ts, delay = item
                delay_by_cam[camera_id] = delay

                save_dir_full = os.path.join(SAVE_DIR_ROOT, f"camera_{camera_id}", "full")
                save_dir_crop = os.path.join(SAVE_DIR_ROOT, f"camera_{camera_id}", "crop")
                os.makedirs(save_dir_full, exist_ok=True)
                os.makedirs(save_dir_crop, exist_ok=True)

                cam_recent = recent_plates_by_cam.setdefault(camera_id, {})
                h_frame, w_frame = frame.shape[:2]

                # ขั้นที่ 1: หา "รถ/มอเตอร์ไซค์" ทั้งเฟรมก่อน ด้วย YOLO pretrained (COCO)
                car_results = car_detector(frame, verbose=False)

                for car_result in car_results:
                    for car_box_raw in car_result.boxes:
                        cls_id = int(car_box_raw.cls[0])
                        if cls_id not in CAR_CLASS_IDS:
                            continue

                        cx1, cy1, cx2, cy2 = map(int, car_box_raw.xyxy[0])
                        car_w, car_h = cx2 - cx1, cy2 - cy1

                        pad_x = int(car_w * CAR_BOX_EXPAND_RATIO)
                        pad_y = int(car_h * CAR_BOX_EXPAND_RATIO)
                        ex1 = max(0, cx1 - pad_x)
                        ey1 = max(0, cy1 - pad_y)
                        ex2 = min(w_frame, cx2 + pad_x)
                        ey2 = min(h_frame, cy2 + pad_y)

                        car_crop = frame[ey1:ey2, ex1:ex2]
                        if car_crop.size == 0:
                            continue

                        # ขั้นที่ 2: หาป้ายทะเบียน "เฉพาะในกรอบรถ" ด้วย YOLO ที่เทรนเอง
                        plate_results = yolo_model(car_crop, verbose=False)

                        valid_plates = []
                        for plate_result in plate_results:
                            for pbox in plate_result.boxes:
                                px1, py1, px2, py2 = map(int, pbox.xyxy[0])
                                conf = pbox.conf[0].item()
                                width, height = px2 - px1, py2 - py1
                                aspect = width / height if height > 0 else 0

                                if aspect < MIN_ASPECT_RATIO or width < MIN_WIDTH:
                                    continue
                                if conf <= YOLO_CONF:
                                    continue

                                valid_plates.append((px1, py1, px2, py2, conf))

                        if not valid_plates:
                            continue

                        # ขั้นที่ 3: ทายสีรถ — ทำเฉพาะตอนเจอป้ายแล้วเท่านั้น
                        color = detect_car_color(color_model, color_class_names, car_crop, logger)

                        for px1, py1, px2, py2, plate_conf in valid_plates:
                            plate_crop, enhanced_gray = preprocess_plate(car_crop, px1, py1, px2, py2)
                            plate, province, ocr_conf = read_plate(plate_crop, enhanced_gray, logger)

                            if plate:
                                current_ts = time.time()
                                # ตรวจสอบ Cooldown และประเมิน Confidence Upgrade
                                status, matched_plate, time_diff, rec_entry = check_recent_plate(cam_recent, plate, current_ts, delay)
                                if status == "exact":
                                    continue

                                combined_score = ocr_conf * 0.7 + plate_conf * 0.3

                                if status == "similar" and isinstance(rec_entry, dict) and "event_id" in rec_entry:
                                    prev_conf = rec_entry.get("ocr_conf", 0.0)
                                    prev_score = rec_entry.get("combined_score", 0.0)

                                    # เกณฑ์อัปเกรด: ป้ายเดิมยังไม่แตะระดับสูงสุด (0.9980), ค่าใหม่ต้องชนะค่าเดิมจริงในระดับทศนิยมที่แสดงผล, และมั่นใจสูง
                                    should_upgrade = (
                                        prev_conf < 0.9980 and
                                        ocr_conf > prev_conf and
                                        round(ocr_conf * 100, 1) > round(prev_conf * 100, 1) and
                                        (ocr_conf >= 0.95 or combined_score > prev_score)
                                    )

                                    if should_upgrade:
                                        event_id = rec_entry["event_id"]
                                        old_plate = rec_entry["plate"]
                                        now_upgrade = time.time()
                                        logger.info(
                                            f"[กล้อง {camera_id}] [Confidence Upgrade] อัปเกรดป้ายทะเบียน: {old_plate} -> {plate} "
                                            f"(ความมั่นใจ OCR {ocr_conf * 100:.1f}% > {prev_conf * 100:.1f}%)"
                                        )
                                        # อัปเดตรูปภาพ, ฐานข้อมูล และ Webhook
                                        save_capture(
                                            camera_id, frame, plate_crop, plate, province, color,
                                            save_dir_full, save_dir_crop, logger,
                                            io_executor=io_executor,
                                            event_id=event_id,
                                            is_update=True,
                                        )
                                        # อัปเดตข้อมูลในแคช Cooldown แทนที่ป้ายเดิม
                                        cam_recent.pop(old_plate, None)
                                        cam_recent[plate] = {
                                            "timestamp": now_upgrade,
                                            "event_id": event_id,
                                            "plate": plate,
                                            "province": province,
                                            "color": color,
                                            "ocr_conf": ocr_conf,
                                            "plate_conf": plate_conf,
                                            "combined_score": combined_score,
                                            "frame_count": rec_entry.get("frame_count", 1) + 1,
                                        }
                                        continue
                                    else:
                                        sim = calculate_plate_similarity(plate, matched_plate) * 100
                                        logger.info(
                                            f"[กล้อง {camera_id}] ข้ามป้าย {plate} — คล้ายกับป้าย {matched_plate} ({sim:.1f}%) "
                                            f"ที่เพิ่งบันทึกไป {time_diff:.1f} วิ ก่อนหน้า (ความมั่นใจ {ocr_conf * 100:.1f}% ไม่ผ่านเกณฑ์อัปเกรด)"
                                        )
                                        continue

                                candidate = {
                                    "plate": plate,
                                    "province": province,
                                    "color": color,
                                    "ocr_conf": ocr_conf,
                                    "plate_conf": plate_conf,
                                    "combined_score": combined_score,
                                    "frame": frame,
                                    "plate_crop": plate_crop,
                                    "timestamp": current_ts,
                                }

                                # นำเข้าตะกร้าที่ตรงกัน หรือสร้างตะกร้าใหม่
                                clusters = active_clusters_by_cam.setdefault(camera_id, [])
                                matched_cluster = None
                                for cl in clusters:
                                    if cl.matches(plate):
                                        matched_cluster = cl
                                        break

                                if matched_cluster:
                                    matched_cluster.add_candidate(candidate, current_ts)
                                else:
                                    clusters.append(PlateCluster(camera_id, candidate, current_ts))

            now_check = time.time()
            # [ตรวจสอบและปิดตะกร้าที่หมดเวลาสะสม (2.5 วินาที)]:
            for cid, clusters in list(active_clusters_by_cam.items()):
                remaining_clusters = []
                c_recent = recent_plates_by_cam.setdefault(cid, {})
                c_save_full = os.path.join(SAVE_DIR_ROOT, f"camera_{cid}", "full")
                c_save_crop = os.path.join(SAVE_DIR_ROOT, f"camera_{cid}", "crop")
                c_delay = delay_by_cam.get(cid, 10.0)

                for cl in clusters:
                    is_expired = (now_check - cl.start_time >= BEST_SHOT_WINDOW_SECONDS)
                    if is_expired:
                        finalize_cluster(
                            cl, c_save_full, c_save_crop, logger, io_executor, c_recent, c_delay
                        )
                    else:
                        remaining_clusters.append(cl)

                active_clusters_by_cam[cid] = remaining_clusters

            # [ล้างแคชป้ายเก่า]: ล้าง entry ที่พ้นระยะ delay ไปแล้วทุกๆ 60 วินาที
            if (now - last_cleanup_time) > 60:
                for cid, p_dict in list(recent_plates_by_cam.items()):
                    recent_plates_by_cam[cid] = {
                        p: entry for p, entry in p_dict.items()
                        if (now - (entry["timestamp"] if isinstance(entry, dict) else entry)) < 300
                    }
                last_cleanup_time = now

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # ปิดตะกร้าที่ยังค้างอยู่ก่อนปิดระบบ
        now = time.time()
        for cid, clusters in list(active_clusters_by_cam.items()):
            c_recent = recent_plates_by_cam.setdefault(cid, {})
            c_save_full = os.path.join(SAVE_DIR_ROOT, f"camera_{cid}", "full")
            c_save_crop = os.path.join(SAVE_DIR_ROOT, f"camera_{cid}", "crop")
            c_delay = delay_by_cam.get(cid, 10.0)
            for cl in clusters:
                finalize_cluster(cl, c_save_full, c_save_crop, logger, io_executor, c_recent, c_delay)

        io_executor.shutdown(wait=False)
        logger.info("Central Inference Worker หยุดการทำงานเรียบร้อย")


if __name__ == "__main__":
    raise SystemExit(
        "ห้ามรันไฟล์นี้ตรงๆ — ไฟล์นี้ถูกออกแบบให้ camera_manager.py เป็นคน spawn เท่านั้น "
        "ถ้าต้องการรันดูภาพสดทีละกล้อง ให้ใช้ main_rtsp2.py หรือ test_model4.py แทน"
    )