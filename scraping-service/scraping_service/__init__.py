import sys
import asyncio

if sys.platform == 'win32':
    # This must be done BEFORE any loop is created or used by Playwright or other async libs.
    # Setting it here in the package root ensures it's one of the first things imported.
    try:
        if not isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass
