"""ทางเดียวที่ camera แตะฐานข้อมูล: ดึงรายชื่อกล้องที่ต้องเฝ้าตอนนี้ (camera_manager เรียกทุก 5 วินาที)

[ทำไมเขียนเป็น SQL ตรงๆ แทนการ import smartlpr.models ของ backend]:
camera กับ backend เป็นคนละโปรเจคแล้ว ถ้า camera ต้อง copy models.py ทั้งไฟล์ (ทุกตาราง) มาไว้ด้วย
ทุกครั้งที่ backend แก้ตารางอะไรก็ตาม ต้องตามแก้ 2 ที่ ลืมเมื่อไหร่ camera พัง
query ข้างล่างใช้แค่ 3 ตาราง 9 คอลัมน์ — backend จะแก้ตารางอื่น/คอลัมน์อื่นยังไงก็ไม่กระทบ camera
(ต้องแก้ไฟล์นี้ตามก็ต่อเมื่อเปลี่ยนชื่อคอลัมน์ที่อยู่ใน query นี้เท่านั้น)

[ทำไมใช้ NullPool]: camera_manager fork โปรเซสลูก (streamer / AI worker) ออกไปหลายตัว ถ้าเก็บ connection
ค้างไว้ใน pool ลูกทุกตัวจะได้ socket เดียวกันติดไปด้วย NullPool = เปิด connection ใหม่ทุกครั้งที่ถาม
แล้วปิดทันที (ถามแค่ทุก 5 วินาที ไม่หนักอะไร) ไม่มี connection ค้างให้ลูกแย่งกันใช้
"""
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from camera.config import DATABASE_URL

# DATABASE_URL ใช้ค่าเดียวกับ backend (driver แบบ async: asyncpg) แต่ camera เป็นโค้ดแบบ sync
# จึงแปลงเป็น driver sync (psycopg2) ก่อนใช้
_ASYNC_TO_SYNC_PREFIX = {
    "postgresql+asyncpg://": "postgresql+psycopg2://",
    "postgres+asyncpg://": "postgresql+psycopg2://",
    "sqlite+aiosqlite://": "sqlite://",
}


def _to_sync_url(url: str) -> str:
    for async_prefix, sync_prefix in _ASYNC_TO_SYNC_PREFIX.items():
        if url.startswith(async_prefix):
            return sync_prefix + url[len(async_prefix):]
    return url


engine = create_engine(_to_sync_url(DATABASE_URL), poolclass=NullPool)

# กล้องที่ต้องเฝ้า = เปิดใช้งานอยู่ + เจ้าของไม่ถูกระงับ + webhook ที่ผูกไว้ยังเปิดอยู่
# (เงื่อนไขเดียวกับ query เดิมใน camera_manager.py ทุกประการ)
ACTIVE_CAMERAS_SQL = text("""
    SELECT c.id, c.rtsp_url, c.delay
    FROM cameras c
    JOIN users u             ON u.id = c.owner_user_id
    JOIN webhook_endpoints w ON w.id = c.webhook_endpoint_id
    WHERE c.is_active = TRUE
      AND u.is_suspended = FALSE
      AND w.is_active = TRUE
""")


def fetch_active_cameras() -> dict[str, dict]:
    """คืน {camera_id: {"rtsp_url": ..., "delay": ...}}"""
    with engine.connect() as conn:
        rows = conn.execute(ACTIVE_CAMERAS_SQL).all()
    return {
        row.id: {
            "rtsp_url": row.rtsp_url,
            "delay": row.delay if row.delay is not None else 1,
        }
        for row in rows
    }
