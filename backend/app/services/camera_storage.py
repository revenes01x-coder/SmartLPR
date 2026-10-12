from __future__ import annotations

import os
import shutil
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("smartlpr.camera_storage")


def delete_camera_storage(camera_id: str, captures_root: str = "captures", logs_root: str = "logs") -> bool:
    """
    ลบโฟลเดอร์รูปภาพ (captures/camera_{camera_id}) และไฟล์ Log (logs/camera_{camera_id}.log)
    ของกล้องที่ถูกลบออกจากระบบอย่างถาวร (Approach A: Immediate Cleanup)
    มีระบบตรวจสอบ Path Traversal เพื่อความปลอดภัย
    """
    if not camera_id or not isinstance(camera_id, str):
        return False

    # ป้องกัน Directory Traversal
    safe_id = os.path.basename(camera_id.strip()).replace("..", "").replace("/", "").replace("\\", "")
    if not safe_id:
        return False

    # 1. ลบโฟลเดอร์รูปภาพ captures/camera_{camera_id}
    captures_dir = os.path.join(captures_root, f"camera_{safe_id}")
    if os.path.isdir(captures_dir):
        try:
            shutil.rmtree(captures_dir, ignore_errors=True)
            logger.info(f"ลบโฟลเดอร์รูปภาพกล้องเรียบร้อย: {captures_dir}")
        except Exception as e:
            logger.warning(f"ลบโฟลเดอร์รูปภาพกล้องไม่สำเร็จ ({captures_dir}): {e}")

    # 2. ลบไฟล์ Log logs/camera_{camera_id}.log
    log_file = os.path.join(logs_root, f"camera_{safe_id}.log")
    if os.path.isfile(log_file):
        try:
            os.remove(log_file)
            logger.info(f"ลบไฟล์ Log กล้องเรียบร้อย: {log_file}")
        except Exception as e:
            logger.warning(f"ลบไฟล์ Log กล้องไม่สำเร็จ ({log_file}): {e}")

    return True


async def prune_orphaned_camera_storage(db: AsyncSession, captures_root: str = "captures", logs_root: str = "logs") -> int:
    """
    สแกนโฟลเดอร์ captures/ และ logs/ แล้วลบไฟล์ของกล้องที่ไม่มีตัวตนอยู่ในฐานข้อมูลแล้ว (Orphaned Assets)
    คืนจำนวนกล้องที่ถูกเก็บกวาดทิ้ง
    """
    from sqlalchemy import select
    from smartlpr import models

    try:
        result = await db.execute(select(models.Camera.id))
        active_camera_ids = {str(cid) for (cid,) in result.all()}
    except Exception as e:
        logger.error(f"ไม่สามารถดึงรายชื่อกล้องจาก DB เพื่อ prune storage ได้: {e}")
        return 0

    pruned_count = 0
    candidate_ids = set()

    # 1. หา camera_id จากโฟลเดอร์ใน captures/
    if os.path.isdir(captures_root):
        try:
            for entry in os.listdir(captures_root):
                if entry.startswith("camera_") and os.path.isdir(os.path.join(captures_root, entry)):
                    cam_id = entry[len("camera_"):]
                    if cam_id not in active_camera_ids:
                        candidate_ids.add(cam_id)
        except Exception as e:
            logger.warning(f"อ่านโฟลเดอร์ {captures_root} ไม่สำเร็จ: {e}")

    # 2. หา camera_id จากไฟล์ใน logs/
    if os.path.isdir(logs_root):
        try:
            for entry in os.listdir(logs_root):
                if entry.startswith("camera_") and entry.endswith(".log"):
                    cam_id = entry[len("camera_"):-len(".log")]
                    if cam_id not in active_camera_ids:
                        candidate_ids.add(cam_id)
        except Exception as e:
            logger.warning(f"อ่านโฟลเดอร์ {logs_root} ไม่สำเร็จ: {e}")

    # 3. ลบไฟล์ของกล้องที่ไม่มีใน DB ทิ้งทั้งหมด
    for cam_id in candidate_ids:
        if delete_camera_storage(cam_id, captures_root, logs_root):
            pruned_count += 1

    if pruned_count:
        logger.info(f"เก็บกวาดไฟล์และ Log ของกล้องที่ไม่มีในระบบแล้วเสร็จสิ้น: {pruned_count} ตัว")

    return pruned_count
