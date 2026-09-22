import asyncio
from smartlpr.database import SessionLocal
from smartlpr.models import WebhookEvent
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        result = await db.execute(select(WebhookEvent).filter(WebhookEvent.status == 'pending'))
        pending = result.scalars().all()
        print(f"Pending webhook events: {len(pending)}")

if __name__ == '__main__':
    asyncio.run(main())
