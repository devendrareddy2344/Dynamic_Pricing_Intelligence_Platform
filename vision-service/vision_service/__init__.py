import sys
import asyncio

if sys.platform == 'win32':
    # This must be done BEFORE any loop is created or used by Playwright or other async libs.
    # Setting it here in the package root ensures it's one of the first things imported.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from vision_service.app import identify_product_from_image

__all__ = ["identify_product_from_image"]
