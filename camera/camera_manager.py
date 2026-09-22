import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
import logging
import multiprocessing as mp

from smartlpr import models
import camera.camera_streamer as camera_streamer
import camera.camera_worker as camera_worker
from smartlpr.database_sync import SessionLocal

POLL_INTERVAL_SECONDS = 5
TERMINATE_TIMEOUT_SECONDS = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("camera_manager")


def get_active_cameras() -> dict[str, dict]:
    """คืน {camera_id: {"rtsp_url": ..., "delay": ...}} เฉพาะกล้องที่ is_active=True และเจ้าของยังไม่ถูกระงับ"""
    db = SessionLocal()
    try:
        cameras = (
            db.query(models.Camera)
            .join(models.User, models.Camera.owner_user_id == models.User.id)
            .join(models.WebhookEndpoint, models.Camera.webhook_endpoint_id == models.WebhookEndpoint.id)
            .filter(
                models.Camera.is_active == True,      # noqa: E712
                models.User.is_suspended == False,     # noqa: E712
                models.WebhookEndpoint.is_active == True,
            )
            .all()
        )
        return {
            c.id: {
                "rtsp_url": c.rtsp_url,
                "delay": c.delay if c.delay is not None else 1,
            }
            for c in cameras
        }
    finally:
        db.close()


def spawn_inference_worker(frame_queue: mp.Queue, stop_event: mp.Event) -> mp.Process:
    """เริ่ม Central AI Inference Worker (โหลดโมเดลทั้ง 4 ตัวไว้ชุดเดียวใน RAM)"""
    process = mp.Process(
        target=camera_worker.run_inference_worker,
        args=(frame_queue, stop_event),
        name="ai-inference-worker",
        daemon=True,
    )
    process.start()
    logger.info(f"เริ่ม Central AI Inference Worker (pid={process.pid})")
    return process


def spawn_camera_process(
    camera_id: str, rtsp_url: str, delay: int, frame_queue: mp.Queue, stop_event: mp.Event
) -> mp.Process:
    """เริ่ม Lightweight Camera Streamer process (ไม่มี AI models, ใช้ RAM ~40MB)"""
    process = mp.Process(
        target=camera_streamer.run,
        args=(camera_id, rtsp_url, delay, frame_queue, stop_event),
        name=f"streamer-{camera_id}",
        daemon=True,
    )
    process.start()
    logger.info(f"เริ่ม streamer process สำหรับกล้อง {camera_id} (pid={process.pid}, delay={delay}s)")
    return process


def _terminate(identifier: str, process: mp.Process, reason: str):
    if process.is_alive():
        logger.info(f"โปรเซส {identifier}: {reason} -> terminate (pid={process.pid})")
        process.terminate()
        process.join(timeout=TERMINATE_TIMEOUT_SECONDS)
        if process.is_alive():
            logger.warning(f"โปรเซส {identifier}: process (pid={process.pid}) ไม่ยอมหยุด -> kill")
            process.kill()
            process.join(timeout=TERMINATE_TIMEOUT_SECONDS)


def main():
    # ตรวจสอบจำนวนกล้องใน Database ตอนเริ่มต้น เพื่อคำนวณขนาดคิวกลางที่เหมาะสม (30 คิว ต่อ 1 ชุด AI)
    try:
        initial_cameras = get_active_cameras()
        initial_cam_count = len(initial_cameras)
    except Exception as e:
        logger.warning(f"ดึงรายชื่อกล้องเริ่มต้นเพื่อคำนวณขนาดคิวไม่สำเร็จ: {e}")
        initial_cam_count = 1

    initial_ai_workers = max(1, (initial_cam_count + 4) // 5)
    queue_size = initial_ai_workers * 30

    frame_queue = mp.Queue(maxsize=queue_size)
    stop_event = mp.Event()

    logger.info(
        f"Camera Manager เริ่มทำงาน (Auto-scaling): "
        f"ตรวจพบกล้องเริ่มต้น {initial_cam_count} ตัว -> กำหนดขนาดคิวกลาง {queue_size} คิว "
        f"(สัดส่วน 30 คิว ต่อ AI 1 ชุด)"
    )

    inference_workers: list[mp.Process] = []

    running: dict[str, mp.Process] = {}          # camera_id -> Process ที่กำลังรันอยู่
    current_configs: dict[str, dict] = {}        # camera_id -> {"rtsp_url": ..., "delay": ...}

    try:
        while True:
            try:
                active_cameras = get_active_cameras()
            except Exception as e:
                logger.error(f"ดึงรายชื่อกล้องจาก DB ไม่สำเร็จ: {e}")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            active_cameras_count = len(active_cameras)
            
            # คำนวณจำนวน Worker ที่ควรมี (1 ชุด ต่อ 5 กล้อง) ไร้ขีดจำกัด
            needed_workers = max(1, (active_cameras_count + 4) // 5)

            # 1. ตรวจสอบสถานะ Worker เก่า และเอาตัวที่ตาย (crash) ออกจาก List
            alive_workers = []
            for w in inference_workers:
                if w.is_alive():
                    alive_workers.append(w)
                else:
                    logger.error(f"Central AI Inference Worker (pid={w.pid}) หยุดทำงานไม่คาดคิด (crash)")
            inference_workers = alive_workers

            # 2. เพิ่ม Worker ถ้าน้อยกว่าที่คำนวณไว้
            while len(inference_workers) < needed_workers:
                new_worker = spawn_inference_worker(frame_queue, stop_event)
                inference_workers.append(new_worker)
                logger.info(f"Auto-scaling: เพิ่มจำนวน AI Worker เป็น {len(inference_workers)} ชุด (รองรับกล้อง {active_cameras_count} ตัว)")

            # 3. ลด Worker ถ้ามากเกินกว่าที่คำนวณไว้ (เช่น ผู้ใช้ลบกล้องออก)
            while len(inference_workers) > needed_workers:
                w_to_remove = inference_workers.pop()
                _terminate("Inference Worker (Scale-down)", w_to_remove, "ลดจำนวน AI ให้พอดีกับปริมาณกล้อง")
                logger.info(f"Auto-scaling: ลดจำนวน AI Worker เหลือ {len(inference_workers)} ชุด")

            active_ids = set(active_cameras.keys())
            running_ids = set(running.keys())

            # 1. กล้องที่ถูกปิดใช้งานหรือถูกลบไปแล้ว -> terminate process
            for camera_id in running_ids - active_ids:
                process = running.pop(camera_id)
                current_configs.pop(camera_id, None)
                _terminate(f"กล้อง {camera_id}", process, "ถูกปิดใช้งาน/ลบออกจากระบบ")

            # 2. กล้องที่ config เปลี่ยน (rtsp_url หรือ delay เปลี่ยน) -> restart ด้วย config ใหม่
            for camera_id in running_ids & active_ids:
                old_cfg = current_configs.get(camera_id, {})
                new_cfg = active_cameras[camera_id]
                if old_cfg.get("rtsp_url") != new_cfg["rtsp_url"] or old_cfg.get("delay") != new_cfg["delay"]:
                    reason = "rtsp_url เปลี่ยน" if old_cfg.get("rtsp_url") != new_cfg["rtsp_url"] else "delay เปลี่ยน"
                    _terminate(f"กล้อง {camera_id}", running[camera_id], reason)
                    running[camera_id] = spawn_camera_process(
                        camera_id, new_cfg["rtsp_url"], new_cfg["delay"], frame_queue, stop_event
                    )
                    current_configs[camera_id] = new_cfg

            # 3. กล้องที่ process ตายไปเอง (crash) -> restart
            for camera_id in running_ids & active_ids:
                process = running[camera_id]
                if not process.is_alive():
                    cfg = active_cameras[camera_id]
                    logger.warning(f"กล้อง {camera_id}: streamer process หยุดทำงานไม่คาดคิด (crash) -> restart")
                    running[camera_id] = spawn_camera_process(
                        camera_id, cfg["rtsp_url"], cfg["delay"], frame_queue, stop_event
                    )
                    current_configs[camera_id] = cfg

            # 4. กล้องใหม่ที่ active แต่ยังไม่มี process -> spawn
            for camera_id in active_ids - running_ids:
                cfg = active_cameras[camera_id]
                running[camera_id] = spawn_camera_process(
                    camera_id, cfg["rtsp_url"], cfg["delay"], frame_queue, stop_event
                )
                current_configs[camera_id] = cfg

            time.sleep(POLL_INTERVAL_SECONDS)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Camera Manager ได้รับสัญญาณหยุดทำงาน กำลังปิดทุกโปรเซส...")
    finally:
        stop_event.set()
        for camera_id, process in list(running.items()):
            _terminate(f"กล้อง {camera_id}", process, "ปิดระบบ Camera Manager")
        
        for w in inference_workers:
            if w.is_alive():
                try:
                    frame_queue.put_nowait(None)
                except Exception:
                    pass
                _terminate("Central Inference Worker", w, "ปิดระบบ Camera Manager")


if __name__ == "__main__":
    main()
