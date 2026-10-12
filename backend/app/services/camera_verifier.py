import asyncio
import logging
import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
import cv2
from security.camera_url_guard import resolve_rtsp_url_pinned
from security.ip_guard import SSRFBlockedError

logger = logging.getLogger("smartlpr.camera_verifier")

DEFAULT_VERIFY_TIMEOUT = 5


def _try_open_rtsp_sync(rtsp_url: str) -> bool:
    """
    ทดสอบเปิด RTSP และอ่านเฟรมแบบ synchronous (blocking call)
    คืน True ถ้าเชื่อมต่อและอ่านเฟรมสำเร็จ คืน False ถ้าต่อไม่ได้หรือล้มเหลว
    """
    try:
        pinned_url = resolve_rtsp_url_pinned(rtsp_url)
    except SSRFBlockedError as e:
        logger.warning(f"[Camera Verify] ปฏิเสธการตรวจสอบ RTSP (SSRF): {e}")
        return False
    except Exception as e:
        logger.warning(f"[Camera Verify] resolve RTSP URL ผิดพลาด: {e}")
        return False

    cap = None
    try:
        cap = cv2.VideoCapture(pinned_url)
        if not cap.isOpened():
            return False

        for _ in range(3):
            ret, frame = cap.read()
            if ret and frame is not None:
                return True
        return False
    except Exception as e:
        logger.warning(f"[Camera Verify] เปิด RTSP ผิดพลาด: {e}")
        return False
    finally:
        if cap is not None:
            cap.release()


async def check_camera_rtsp(rtsp_url: str, timeout_seconds: int = DEFAULT_VERIFY_TIMEOUT) -> bool:
    """
    ทดสอบเชื่อมต่อ RTSP แบบ Non-blocking พร้อม Timeout
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_try_open_rtsp_sync, rtsp_url),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[Camera Verify] เชื่อมต่อ RTSP หมดเวลา ({timeout_seconds}s): {rtsp_url}")
        return False
    except Exception as e:
        logger.warning(f"[Camera Verify] เกิดข้อผิดพลาดขณะตรวจสอบ RTSP: {e}")
        return False
