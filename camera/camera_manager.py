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
# เวลารอให้โปรเซสหยุดเองหลัง set stop event (streamer อาจติดอยู่ใน reconnect RTSP ได้นานสุด ~RECONNECT_SEC)
GRACEFUL_STOP_TIMEOUT_SECONDS = 30

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


def spawn_inference_worker(frame_queue: mp.Queue) -> tuple[mp.Process, mp.Event]:
    """เริ่ม Central AI Inference Worker (โหลดโมเดลทั้ง 4 ตัวไว้ชุดเดียวใน RAM)
    แต่ละ worker มี stop event ของตัวเอง ใช้สั่งหยุดทีละตัวแบบ graceful ตอน scale-down"""
    stop_event = mp.Event()
    process = mp.Process(
        target=camera_worker.run_inference_worker,
        args=(frame_queue, stop_event),
        name="ai-inference-worker",
        daemon=True,
    )
    process.start()
    logger.info(f"เริ่ม Central AI Inference Worker (pid={process.pid})")
    return process, stop_event


def spawn_camera_process(
    camera_id: str, rtsp_url: str, delay: int, frame_queue: mp.Queue
) -> tuple[mp.Process, mp.Event]:
    """เริ่ม Lightweight Camera Streamer process (ไม่มี AI models, ใช้ RAM ~40MB)
    แต่ละกล้องมี stop event ของตัวเอง ใช้สั่งหยุดทีละกล้องแบบ graceful"""
    stop_event = mp.Event()
    process = mp.Process(
        target=camera_streamer.run,
        args=(camera_id, rtsp_url, delay, frame_queue, stop_event),
        name=f"streamer-{camera_id}",
        daemon=True,
    )
    process.start()
    logger.info(f"เริ่ม streamer process สำหรับกล้อง {camera_id} (pid={process.pid}, delay={delay}s)")
    return process, stop_event


def _stop_processes(items: list[tuple[str, mp.Process, mp.Event]], reason: str) -> bool:
    """สั่งหยุดโปรเซสแบบ graceful (set stop event แล้วรอให้ออกเอง) — ทุกตัวพร้อมกัน

    [สำคัญ]: ห้ามใช้ process.terminate() เป็นวิธีหลัก เพราะทุกโปรเซสใช้ frame_queue (mp.Queue)
    ตัวเดียวกัน ถ้า terminate ตอนโปรเซสนั้นถือ lock ของคิวอยู่ (กำลัง put เฟรม หรือกำลัง get)
    lock จะค้างถาวร -> โปรเซสอื่นทั้งหมดส่ง/รับเฟรมไม่ได้อีกเลย การตรวจจับป้ายของทุกกล้องหยุด
    ทั้งที่โปรเซสยังไม่ตาย (camera_manager มองไม่เห็นว่าเสีย) ดูเอกสาร Python:
    multiprocessing.Process.terminate()

    คืนค่า True ถ้าทุกตัวหยุดเองได้, False ถ้ามีตัวที่ต้องบังคับ kill (คิวอาจเสียแล้ว)
    """
    for identifier, process, stop_event in items:
        if process.is_alive():
            logger.info(f"โปรเซส {identifier}: {reason} -> สั่งหยุด (pid={process.pid})")
        stop_event.set()

    deadline = time.monotonic() + GRACEFUL_STOP_TIMEOUT_SECONDS
    clean = True
    for identifier, process, _ in items:
        process.join(timeout=max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            clean = False
            logger.error(
                f"โปรเซส {identifier}: (pid={process.pid}) ไม่ยอมหยุดภายใน "
                f"{GRACEFUL_STOP_TIMEOUT_SECONDS} วิ -> บังคับ kill (frame_queue อาจเสีย)"
            )
            process.kill()
            process.join(timeout=TERMINATE_TIMEOUT_SECONDS)
    return clean


def _died_abnormally(process: mp.Process) -> bool:
    """โปรเซสตายด้วย signal (เช่น OOM killer / segfault) -> exitcode ติดลบ อาจตายคา lock ของคิว"""
    return process.exitcode is not None and process.exitcode < 0


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

    logger.info(
        f"Camera Manager เริ่มทำงาน (Auto-scaling): "
        f"ตรวจพบกล้องเริ่มต้น {initial_cam_count} ตัว -> กำหนดขนาดคิวกลาง {queue_size} คิว "
        f"(สัดส่วน 30 คิว ต่อ AI 1 ชุด)"
    )

    inference_workers: list[tuple[mp.Process, mp.Event]] = []

    running: dict[str, tuple[mp.Process, mp.Event]] = {}   # camera_id -> (Process, stop_event)
    current_configs: dict[str, dict] = {}                  # camera_id -> {"rtsp_url": ..., "delay": ...}

    try:
        while True:
            try:
                active_cameras = get_active_cameras()
            except Exception as e:
                logger.error(f"ดึงรายชื่อกล้องจาก DB ไม่สำเร็จ: {e}")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # ถ้ารอบนี้มีโปรเซสที่ต้องบังคับ kill / ตายด้วย signal -> frame_queue อาจเสีย ต้องสร้างใหม่ทั้งชุด
            queue_tainted = False

            active_cameras_count = len(active_cameras)

            # คำนวณจำนวน Worker ที่ควรมี (1 ชุด ต่อ 5 กล้อง) ไร้ขีดจำกัด
            needed_workers = max(1, (active_cameras_count + 4) // 5)

            # 1. ตรวจสอบสถานะ Worker เก่า และเอาตัวที่ตาย (crash) ออกจาก List
            alive_workers = []
            for w, ev in inference_workers:
                if w.is_alive():
                    alive_workers.append((w, ev))
                else:
                    logger.error(
                        f"Central AI Inference Worker (pid={w.pid}) หยุดทำงานไม่คาดคิด (crash, exitcode={w.exitcode})"
                    )
                    if _died_abnormally(w):
                        queue_tainted = True
            inference_workers = alive_workers

            # 2. ลด Worker ถ้ามากเกินกว่าที่คำนวณไว้ (เช่น ผู้ใช้ลบกล้องออก) — หยุดแบบ graceful
            if len(inference_workers) > needed_workers:
                to_remove = inference_workers[needed_workers:]
                inference_workers = inference_workers[:needed_workers]
                if not _stop_processes(
                    [("Inference Worker (Scale-down)", w, ev) for w, ev in to_remove],
                    "ลดจำนวน AI ให้พอดีกับปริมาณกล้อง",
                ):
                    queue_tainted = True
                logger.info(f"Auto-scaling: ลดจำนวน AI Worker เหลือ {len(inference_workers)} ชุด")

            active_ids = set(active_cameras.keys())
            running_ids = set(running.keys())

            # 3. กล้องที่ถูกปิดใช้งานหรือถูกลบไปแล้ว + กล้องที่ config เปลี่ยน -> หยุดแบบ graceful
            to_stop = []
            for camera_id in running_ids - active_ids:
                process, ev = running.pop(camera_id)
                current_configs.pop(camera_id, None)
                to_stop.append((f"กล้อง {camera_id} (ถูกปิดใช้งาน/ลบออกจากระบบ)", process, ev))

            for camera_id in running_ids & active_ids:
                old_cfg = current_configs.get(camera_id, {})
                new_cfg = active_cameras[camera_id]
                if old_cfg.get("rtsp_url") != new_cfg["rtsp_url"] or old_cfg.get("delay") != new_cfg["delay"]:
                    reason = "rtsp_url เปลี่ยน" if old_cfg.get("rtsp_url") != new_cfg["rtsp_url"] else "delay เปลี่ยน"
                    process, ev = running.pop(camera_id)
                    current_configs.pop(camera_id, None)
                    to_stop.append((f"กล้อง {camera_id} ({reason} -> restart)", process, ev))

            if to_stop and not _stop_processes(to_stop, "หยุด streamer"):
                queue_tainted = True

            # 4. กล้องที่ process ตายไปเอง (crash) -> ถอดออก เดี๋ยว spawn ใหม่ในขั้นที่ 6
            for camera_id in list(running.keys()):
                process, _ = running[camera_id]
                if not process.is_alive():
                    logger.warning(
                        f"กล้อง {camera_id}: streamer process หยุดทำงานไม่คาดคิด "
                        f"(crash, exitcode={process.exitcode}) -> restart"
                    )
                    if _died_abnormally(process):
                        queue_tainted = True
                    running.pop(camera_id)
                    current_configs.pop(camera_id, None)

            # 5. frame_queue อาจเสีย -> ปิดทุกโปรเซส แล้วสร้างคิวใหม่ทั้งชุด (กันการตรวจจับค้างถาวร)
            if queue_tainted:
                logger.error(
                    "frame_queue อาจเสียจากโปรเซสที่ถูกบังคับ kill/ตายกลางคัน -> "
                    "รีสตาร์ท pipeline ทั้งชุด (streamer + AI worker) ด้วยคิวใหม่"
                )
                _stop_processes(
                    [(f"กล้อง {cid}", p, ev) for cid, (p, ev) in running.items()]
                    + [("Inference Worker", w, ev) for w, ev in inference_workers],
                    "รีสตาร์ท pipeline",
                )
                running.clear()
                current_configs.clear()
                inference_workers = []
                frame_queue = mp.Queue(maxsize=queue_size)

            # 6. เพิ่ม Worker ถ้าน้อยกว่าที่คำนวณไว้
            while len(inference_workers) < needed_workers:
                inference_workers.append(spawn_inference_worker(frame_queue))
                logger.info(f"Auto-scaling: เพิ่มจำนวน AI Worker เป็น {len(inference_workers)} ชุด (รองรับกล้อง {active_cameras_count} ตัว)")

            # 7. กล้องที่ active แต่ยังไม่มี process (ใหม่ / config เปลี่ยน / crash) -> spawn
            for camera_id in set(active_cameras.keys()) - set(running.keys()):
                cfg = active_cameras[camera_id]
                running[camera_id] = spawn_camera_process(
                    camera_id, cfg["rtsp_url"], cfg["delay"], frame_queue
                )
                current_configs[camera_id] = cfg

            time.sleep(POLL_INTERVAL_SECONDS)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Camera Manager ได้รับสัญญาณหยุดทำงาน กำลังปิดทุกโปรเซส...")
    finally:
        _stop_processes(
            [(f"กล้อง {cid}", p, ev) for cid, (p, ev) in running.items()],
            "ปิดระบบ Camera Manager",
        )
        _stop_processes(
            [("Central Inference Worker", w, ev) for w, ev in inference_workers],
            "ปิดระบบ Camera Manager",
        )


if __name__ == "__main__":
    main()
