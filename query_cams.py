import asyncio
from sqlalchemy import select
from smartlpr.database import SessionLocal
from smartlpr.models import Camera

async def main():
    async with SessionLocal() as db:
        result = await db.execute(select(Camera))
        cams = result.scalars().all()
        for c in cams:
            print(f"ID: {c.id}, Camera ID: {c.camera_id}, Owner ID: {c.owner_user_id}, RTSP: {c.rtsp_url}, Status: {c.is_active}")

asyncio.run(main())
