from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
import logging
import multiprocessing as mp

from smartlpr import models
# [Memory]: import เฉพาะ streamer (เบา มีแค่ OpenCV) — ห้าม import camera.camera_worker ที่ระดับ module
# เพราะไฟล์นั้นโหลด TensorFlow + โมเดล OCR (~233MB) ทันทีตอน import ทำให้โปรเซส manager ถือโมเดล
# ไว้เปล่าๆ และทุกโปรเซสลูกที่ fork ออกไป (streamer ทุกตัว) ได้ของพวกนี้ติดไปด้วย
# ตัว AI worker จะ import camera_worker เองภายในโปรเซสลูก (ดู _run_inference_worker)
import camera.camera_streamer as camera_streamer
from smartlpr.database_sync import SessionLocal

POLL_INTERVAL_SECONDS = 5
TERMINATE_TIMEOUT_SECONDS = 10
# เวลารอให้โปรเซสหยุดเองหลัง set stop event (streamer อาจติดอยู่ใน reconnect RTSP ได้นานสุด ~RECONNECT_SEC)
GRACEFUL_STOP_TIMEOUT_SECONDS = 30

# จำนวนกล้องต่อ AI worker 1 ชุด และขนาดคิวของ worker แต่ละชุด
CAMERAS_PER_WORKER = 5
QUEUE_SIZE_PER_WORKER = 30

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


def _run_inference_worker(frame_queue: mp.Queue, stop_event: mp.Event) -> None:
    """Entry point ของโปรเซส AI worker — import camera_worker (TensorFlow/PyTorch/โมเดลทั้งหมด)
    ตรงนี้ ภายในโปรเซสลูกเท่านั้น ไม่ให้โปรเซส manager ต้องโหลดตามไปด้วย"""
    from camera import camera_worker
    camera_worker.run_inference_worker(frame_queue, stop_event)


class _WorkerSlot:
    """AI worker 1 ชุด + คิวเฟรมของตัวเอง

    [ทำไมต้องแยกคิวต่อ worker]: state ของ best-shot cluster / cooldown กันป้ายซ้ำ / confidence
    upgrade เก็บอยู่ใน memory ของ worker แต่ละตัว ถ้าทุก worker ดึงเฟรมจากคิวกลางคิวเดียว เฟรมของรถคัน
    เดียวกันจะกระจายไปหลาย worker -> แต่ละตัวสรุปผลและยิง webhook เองคนละรอบ = ลูกค้าได้ event ซ้ำ
    จึงผูก "กล้อง 1 ตัว -> worker 1 ชุด" เสมอ (streamer ของกล้องนั้นส่งเฟรมเข้าคิวของ slot ที่ถูก assign)

    index = ตำแหน่งใน list slots เสมอ (เพิ่ม/ลดจากท้าย list เท่านั้น)"""

    def __init__(self, index: int):
        self.index = index
        self.queue: mp.Queue = mp.Queue(maxsize=QUEUE_SIZE_PER_WORKER)
        self.process: mp.Process | None = None
        self.stop_event: mp.Event | None = None

    @property
    def label(self) -> str:
        return f"Inference Worker #{self.index}"

    def spawn(self) -> None:
        self.stop_event = mp.Event()
        self.process = mp.Process(
            target=_run_inference_worker,
            args=(self.queue, self.stop_event),
            name=f"ai-inference-worker-{self.index}",
            daemon=True,
        )
        self.process.start()
        logger.info(f"เริ่ม {self.label} (pid={self.process.pid})")

    def forget_process(self) -> None:
        self.process = None
        self.stop_event = None

    def replace_queue(self) -> None:
        """ทิ้งคิวเดิม (อาจมี lock ค้างจากโปรเซสที่ตายกลางคัน) แล้วสร้างใหม่"""
        self.queue = mp.Queue(maxsize=QUEUE_SIZE_PER_WORKER)


def spawn_camera_process(
    camera_id: str, rtsp_url: str, delay: int, frame_queue: mp.Queue
) -> tuple[mp.Process, mp.Event]:
    """เริ่ม Lightweight Camera Streamer process (ไม่มี AI models)
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


def _stop_processes(items: list[tuple[str, mp.Process, mp.Event]], reason: str) -> set[int]:
    """สั่งหยุดโปรเซสแบบ graceful (set stop event แล้วรอให้ออกเอง) — ทุกตัวพร้อมกัน

    [สำคัญ]: ห้ามใช้ process.terminate() เป็นวิธีหลัก เพราะโปรเซสใช้ frame_queue (mp.Queue)
    ร่วมกัน ถ้า terminate ตอนโปรเซสนั้นถือ lock ของคิวอยู่ (กำลัง put เฟรม หรือกำลัง get)
    lock จะค้างถาวร -> โปรเซสอื่นที่ใช้คิวเดียวกันส่ง/รับเฟรมไม่ได้อีกเลย การตรวจจับป้ายหยุด
    ทั้งที่โปรเซสยังไม่ตาย (camera_manager มองไม่เห็นว่าเสีย) ดูเอกสาร Python:
    multiprocessing.Process.terminate()

    คืน set ของตำแหน่ง (index ใน items) ที่ต้องบังคับ kill — set ว่าง = ทุกตัวหยุดเองได้
    ถ้าไม่ว่าง คิวที่โปรเซสตัวนั้นใช้อยู่อาจเสียแล้ว caller ต้องสร้างคิวใหม่
    """
    for identifier, process, stop_event in items:
        if process.is_alive():
            logger.info(f"โปรเซส {identifier}: {reason} -> สั่งหยุด (pid={process.pid})")
        stop_event.set()

    deadline = time.monotonic() + GRACEFUL_STOP_TIMEOUT_SECONDS
    killed: set[int] = set()
    for i, (identifier, process, _) in enumerate(items):
        process.join(timeout=max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            killed.add(i)
            logger.error(
                f"โปรเซส {identifier}: (pid={process.pid}) ไม่ยอมหยุดภายใน "
                f"{GRACEFUL_STOP_TIMEOUT_SECONDS} วิ -> บังคับ kill (frame_queue อาจเสีย)"
            )
            process.kill()
            process.join(timeout=TERMINATE_TIMEOUT_SECONDS)
    return killed


def _died_abnormally(process: mp.Process) -> bool:
    """โปรเซสตายด้วย signal (เช่น OOM killer / segfault) -> exitcode ติดลบ อาจตายคา lock ของคิว"""
    return process.exitcode is not None and process.exitcode < 0


def _pick_slot(slots: list[_WorkerSlot], running: dict[str, tuple[mp.Process, mp.Event, int]]) -> int:
    """เลือก worker ที่มีกล้องน้อยที่สุดให้กล้องตัวใหม่ (เท่ากัน -> เลือก index ต่ำสุด)"""
    load = {slot.index: 0 for slot in slots}
    for _, _, slot_index in running.values():
        if slot_index in load:
            load[slot_index] += 1
    return min(load, key=lambda idx: (load[idx], idx))


def _pop_streamers(
    running: dict[str, tuple[mp.Process, mp.Event, int]],
    current_configs: dict[str, dict],
    camera_ids,
    note: str,
) -> list[tuple[str, mp.Process, mp.Event, int]]:
    """ถอดกล้องออกจาก running คืน [(label, process, stop_event, slot_index)]"""
    popped = []
    for camera_id in camera_ids:
        process, ev, slot_index = running.pop(camera_id)
        current_configs.pop(camera_id, None)
        popped.append((f"กล้อง {camera_id} ({note})", process, ev, slot_index))
    return popped


def _stop_streamers(popped: list[tuple[str, mp.Process, mp.Event, int]], reason: str) -> set[int]:
    """หยุด streamer ตามรายการ คืน set ของ slot index ที่คิวอาจเสีย (มี streamer ถูก kill)"""
    if not popped:
        return set()
    killed = _stop_processes([(label, p, ev) for label, p, ev, _ in popped], reason)
    return {popped[i][3] for i in killed}


def main():
    slots: list[_WorkerSlot] = []
    # camera_id -> (Process, stop_event, slot_index ของ worker ที่กล้องนี้ส่งเฟรมไปให้)
    running: dict[str, tuple[mp.Process, mp.Event, int]] = {}
    current_configs: dict[str, dict] = {}                  # camera_id -> {"rtsp_url": ..., "delay": ...}

    logger.info(
        f"Camera Manager เริ่มทำงาน (Auto-scaling): AI worker 1 ชุดต่อกล้อง {CAMERAS_PER_WORKER} ตัว, "
        f"คิวเฟรม {QUEUE_SIZE_PER_WORKER} ช่องต่อ worker (กล้องแต่ละตัวผูกกับ worker ชุดเดียวเสมอ)"
    )

    try:
        while True:
            try:
                active_cameras = get_active_cameras()
            except Exception as e:
                logger.error(f"ดึงรายชื่อกล้องจาก DB ไม่สำเร็จ: {e}")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # slot ที่คิวอาจเสีย (มีโปรเซสถูกบังคับ kill / ตายด้วย signal) -> ต้องรีสตาร์ททั้ง slot ด้วยคิวใหม่
            tainted_slots: set[int] = set()

            active_cameras_count = len(active_cameras)
            needed_workers = max(1, (active_cameras_count + CAMERAS_PER_WORKER - 1) // CAMERAS_PER_WORKER)

            # 1. ตรวจ AI worker ที่ตายเอง (crash) -> ทำเครื่องหมายให้ spawn ใหม่ในขั้นที่ 6
            for slot in slots:
                if slot.process is not None and not slot.process.is_alive():
                    logger.error(
                        f"{slot.label} (pid={slot.process.pid}) หยุดทำงานไม่คาดคิด "
                        f"(crash, exitcode={slot.process.exitcode})"
                    )
                    if _died_abnormally(slot.process):
                        tainted_slots.add(slot.index)
                    slot.forget_process()

            # 2. ลด AI worker ถ้ามากเกินกว่าที่ต้องใช้ (ตัดจากท้าย list) — หยุด streamer ของ slot ที่ถูกตัด
            #    ก่อน (ระหว่างนี้ worker ยังอ่านคิวอยู่ streamer จึงส่งเฟรมที่ค้างให้หมดแล้วออกได้) แล้วค่อยหยุด
            #    worker กล้องพวกนั้นจะถูก assign ไป worker ที่เหลือในขั้นที่ 7 ส่วนคิวของ slot ที่ถูกตัดทิ้งไปเลย
            if len(slots) > needed_workers:
                removed = slots[needed_workers:]
                slots = slots[:needed_workers]
                removed_indexes = {s.index for s in removed}
                moved = [cid for cid, (_, _, idx) in running.items() if idx in removed_indexes]
                _stop_streamers(
                    _pop_streamers(running, current_configs, moved, "ย้ายไป AI worker ชุดอื่น"),
                    "ลดจำนวน AI worker",
                )
                _stop_processes(
                    [(f"{s.label} (Scale-down)", s.process, s.stop_event) for s in removed if s.process is not None],
                    "ลดจำนวน AI ให้พอดีกับปริมาณกล้อง",
                )
                tainted_slots -= removed_indexes
                logger.info(f"Auto-scaling: ลดจำนวน AI Worker เหลือ {len(slots)} ชุด")

            active_ids = set(active_cameras.keys())
            running_ids = set(running.keys())

            # 3. กล้องที่ถูกปิดใช้งานหรือถูกลบไปแล้ว + กล้องที่ config เปลี่ยน -> หยุดแบบ graceful
            to_stop = _pop_streamers(
                running, current_configs, running_ids - active_ids, "ถูกปิดใช้งาน/ลบออกจากระบบ"
            )
            for camera_id in running_ids & active_ids:
                old_cfg = current_configs.get(camera_id, {})
                new_cfg = active_cameras[camera_id]
                if old_cfg.get("rtsp_url") != new_cfg["rtsp_url"] or old_cfg.get("delay") != new_cfg["delay"]:
                    reason = "rtsp_url เปลี่ยน" if old_cfg.get("rtsp_url") != new_cfg["rtsp_url"] else "delay เปลี่ยน"
                    to_stop += _pop_streamers(running, current_configs, [camera_id], f"{reason} -> restart")

            tainted_slots |= _stop_streamers(to_stop, "หยุด streamer")

            # 4. กล้องที่ process ตายไปเอง (crash) -> ถอดออก เดี๋ยว spawn ใหม่ในขั้นที่ 7
            for camera_id in list(running.keys()):
                process, _, slot_index = running[camera_id]
                if not process.is_alive():
                    logger.warning(
                        f"กล้อง {camera_id}: streamer process หยุดทำงานไม่คาดคิด "
                        f"(crash, exitcode={process.exitcode}) -> restart"
                    )
                    if _died_abnormally(process):
                        tainted_slots.add(slot_index)
                    running.pop(camera_id)
                    current_configs.pop(camera_id, None)

            # 5. คิวของ slot ไหนอาจเสีย -> รีสตาร์ทเฉพาะ slot นั้น (worker ชุดอื่นทำงานต่อได้ตามปกติ)
            #    หยุด streamer ก่อน แล้วค่อยหยุด worker (ลำดับเดียวกับตอนปิดระบบ) แล้วสร้างคิวใหม่
            for slot_index in sorted(tainted_slots):
                if slot_index >= len(slots):
                    continue
                slot = slots[slot_index]
                logger.error(
                    f"frame_queue ของ {slot.label} อาจเสียจากโปรเซสที่ถูกบังคับ kill/ตายกลางคัน -> "
                    "รีสตาร์ท worker ชุดนี้พร้อม streamer ที่ผูกอยู่ ด้วยคิวใหม่"
                )
                on_slot = [cid for cid, (_, _, idx) in running.items() if idx == slot_index]
                _stop_streamers(
                    _pop_streamers(running, current_configs, on_slot, "รีสตาร์ทพร้อม AI worker"),
                    "รีสตาร์ท pipeline",
                )
                if slot.process is not None:
                    _stop_processes([(slot.label, slot.process, slot.stop_event)], "รีสตาร์ท pipeline")
                slot.forget_process()
                slot.replace_queue()

            # 6. เพิ่ม AI worker ให้ครบ + spawn ตัวที่ยังไม่มีโปรเซส (ใหม่ / crash / เพิ่งรีสตาร์ท)
            while len(slots) < needed_workers:
                slots.append(_WorkerSlot(len(slots)))
                logger.info(
                    f"Auto-scaling: เพิ่มจำนวน AI Worker เป็น {len(slots)} ชุด "
                    f"(รองรับกล้อง {active_cameras_count} ตัว)"
                )
            for slot in slots:
                if slot.process is None:
                    slot.spawn()

            # 7. กล้องที่ active แต่ยังไม่มี process (ใหม่ / config เปลี่ยน / crash / ถูกย้าย slot) -> spawn
            #    ผูกกับ worker ที่มีกล้องน้อยที่สุด
            for camera_id in sorted(active_ids - set(running.keys())):
                cfg = active_cameras[camera_id]
                slot_index = _pick_slot(slots, running)
                process, ev = spawn_camera_process(
                    camera_id, cfg["rtsp_url"], cfg["delay"], slots[slot_index].queue
                )
                running[camera_id] = (process, ev, slot_index)
                current_configs[camera_id] = cfg

            time.sleep(POLL_INTERVAL_SECONDS)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Camera Manager ได้รับสัญญาณหยุดทำงาน กำลังปิดทุกโปรเซส...")
    finally:
        # หยุด streamer ก่อนเสมอ (worker ยังอ่านคิวอยู่ streamer จึงส่งเฟรมที่ค้างได้หมดแล้วออกเองได้)
        _stop_processes(
            [(f"กล้อง {cid}", p, ev) for cid, (p, ev, _) in running.items()],
            "ปิดระบบ Camera Manager",
        )
        _stop_processes(
            [(slot.label, slot.process, slot.stop_event) for slot in slots if slot.process is not None],
            "ปิดระบบ Camera Manager",
        )


if __name__ == "__main__":
    main()
