import asyncio
from services.email_service import _send_email

try:
    _send_email("revenes01x@gmail.com", "Test from SmartLPR", "<h1>Test</h1>", "Test")
    print("Email sent successfully!")
except Exception as e:
    print(f"Failed: {e}")
