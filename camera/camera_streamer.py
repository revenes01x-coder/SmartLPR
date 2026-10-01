import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay"

import time
import queue
import logging
import threading
import cv2 as cv

from security.camera_url_guard import resolve_rtsp_url_pinned
from security.ip_guard import SSRFBlockedError
from smartlpr.config import CAMERA_DETECTION_FPS

RECONNECT_SEC = 10
DETECTION_FPS = float(CAMERA_DETECTION_FPS or 5.0)

# Timeout ของ FFmpeg ตอนเปิด RTSP และตอนอ่านเฟรม (มิลลิวินาที) — เดิมไม่ได้ตั้ง ทำให้ VideoCapture()
# กับ cap.read() ค้างได้นานมาก (กล้องออฟไลน์/สตรีมเงียบ) จน streamer หยุดตัวไม่ทันเวลาที่ camera_manager
# รอ (GRACEFUL_STOP_TIMEOUT_SECONDS) แล้วโดน kill -> ต้องรีสตาร์ท AI worker ทั้งชุดโดยไม่จำเป็น
OPEN_TIMEOUT_MS = 5000
READ_TIMEOUT_MS = 5000


def _wait_or_stop(stop_event, seconds: float) -> bool:
    """รอ seconds วินาที แต่ตื่นทันทีถ้าถูกสั่งหยุด — คืน True ถ้าถูกสั่งหยุด
    (แทน time.sleep ที่ไม่เห็น stop_event เลยตลอดช่วงที่หลับอยู่)"""
    if stop_event is None:
        time.sleep(seconds)
        return False
    return stop_event.wait(seconds)


def _open_capture(url: str):
    """เปิด VideoCapture ผ่าน FFmpeg พร้อม timeout ตอนเปิด/อ่าน (OpenCV >= 4.6)
    ถ้า OpenCV รุ่นเก่าไม่มี property พวกนี้ ถอยกลับไปเปิดแบบเดิม"""
    open_timeout = getattr(cv, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)#จะได้53 คือเวลารอตอนเปิดภาพกับเวลารอตอนอ่านภาพ getattr เป็นคนไปหาเอง
    read_timeout = getattr(cv, "CAP_PROP_READ_TIMEOUT_MSEC", None)#54 คนเขียนopencvเป็นคนกำหนด
    if open_timeout is None or read_timeout is None:
        return cv.VideoCapture(url)
    return cv.VideoCapture(url, cv.CAP_FFMPEG, [open_timeout, OPEN_TIMEOUT_MS, read_timeout, READ_TIMEOUT_MS]) 


def _setup_logger(camera_id: str) -> logging.Logger:
    os.makedirs("logs", exist_ok=True) #makedirsเครื่องมือสร้างโฟลเดอร์
    logger = logging.getLogger(f"cam_stream_{camera_id}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(f"logs/camera_{camera_id}.log", encoding="utf-8")#encoding="utf-8"คือการบอกให้รองรับทุก
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    return logger


def open_stream(url: str, logger: logging.Logger):
    """เปิด RTSP stream ด้วย IP ที่ resolve + เช็คแล้วเท่านั้น (pin IP กัน DNS rebinding)"""
    try:
        pinned_url = resolve_rtsp_url_pinned(url)
    except SSRFBlockedError as e:
        logger.warning(f"[SSRF Guard] ปฏิเสธการเชื่อมต่อ RTSP: {e}")
        return None

    cap = _open_capture(pinned_url)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv.CAP_PROP_BUFFERSIZE, 1)
    return cap


def _open_stream_with_retry(rtsp_url: str, logger: logging.Logger, stop_event=None):
    cap = open_stream(rtsp_url, logger)
    while cap is None:
        if stop_event and stop_event.is_set():
            return None
        logger.warning(f"เชื่อมต่อ RTSP ไม่ได้ — รอ {RECONNECT_SEC} วินาทีแล้วลองใหม่...")
        if _wait_or_stop(stop_event, RECONNECT_SEC):
            return None
        cap = open_stream(rtsp_url, logger)
    return cap


class RealtimeVideoStream:
    """RTSP Stream Reader แบบ Real-time (Zero-Latency)
    ดึงภาพสดอย่างต่อเนื่องเพื่อระบาย Buffer ของ FFmpeg และเก็บเฉพาะเฟรมล่าสุดไว้เสมอ"""
    def __init__(self, rtsp_url: str, logger: logging.Logger, stop_event=None):
        self.rtsp_url = rtsp_url
        self.logger = logger
        self.stop_event = stop_event
        self.cap = None
        self.frame = None
        self.ret = False
        self.running = False
        self.lock = threading.Lock()
        self.thread = None
        self._start()

    def _start(self):
        self.cap = _open_stream_with_retry(self.rtsp_url, self.logger, self.stop_event)
        if self.cap is None:
            return
        ret, frame = self.cap.read()
        if ret and frame is not None:
            self.frame = frame
            self.ret = True
        self.running = True
        self.thread = threading.Thread(target=self._capture_worker, daemon=True)
        self.thread.start()

    def _capture_worker(self):
        while self.running:
            if self.stop_event and self.stop_event.is_set():
                break
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
                if _wait_or_stop(self.stop_event, RECONNECT_SEC) or not self.running:
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
                if _wait_or_stop(self.stop_event, RECONNECT_SEC) or not self.running:
                    break
                self.cap = open_stream(self.rtsp_url, self.logger)
                if self.cap and self.cap.isOpened():
                    self.logger.info("เชื่อมต่อ RTSP สำเร็จ กำลังรับภาพต่อ...")
                continue

            with self.lock:
                self.frame = frame
                self.ret = True

    def read(self):
        with self.lock:
            if not self.ret or self.frame is None:
                return False, None
            return True, self.frame.copy()

    def release(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
            if self.thread.is_alive():
                # thread ยังค้างอยู่ใน cap.read() (RTSP ค้าง) -> ห้าม release cap ซ้อนจากอีก thread
                # (อาจค้าง/crash) ปล่อยให้ OS เก็บกวาดตอนโปรเซสจบ เพื่อให้โปรเซสหยุดได้ทันเวลา
                return
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None


def run(camera_id: str, rtsp_url: str, delay: int, frame_queue, stop_event=None):
    """Entry point สำหรับ lightweight camera streamer process
    ดึงภาพจาก RTSP และส่งเฟรมตาม DETECTION_FPS เข้า frame_queue (ไม่โหลดโมเดล AI ใดๆ)"""
    logger = _setup_logger(camera_id)
    detection_interval = 1.0 / max(0.5, float(DETECTION_FPS))
    last_detection_time = 0.0

    logger.info(
        f"[Camera Streamer] เริ่มดึงภาพ RTSP (กล้อง: {camera_id}, delay: {delay}s, {DETECTION_FPS} FPS)"
    )

    stream = RealtimeVideoStream(rtsp_url, logger, stop_event=stop_event)

    try:
        while True:
            if stop_event and stop_event.is_set():
                break

            ret, frame = stream.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            now = time.time()
            if (now - last_detection_time) < detection_interval:
                sleep_time = max(0.005, min(0.03, detection_interval - (now - last_detection_time)))
                time.sleep(sleep_time)
                continue

            last_detection_time = now

            # ส่งเฟรมเข้า Queue ส่วนกลาง (Non-blocking: ถ้าคิวเต็ม ข้ามเฟรมนี้เพื่อรักษา Zero-Latency)
            payload = (camera_id, frame, now, delay)
            try:
                frame_queue.put_nowait(payload)
            except queue.Full:
                # คิวเต็ม: AI ยังประมวลผลเฟรมก่อนหน้าอยู่ ข้ามเพื่อไม่ให้คิวสะสม
                pass

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        stream.release()
        logger.info(f"[Camera Streamer] หยุดการทำงานกล้อง {camera_id}")

