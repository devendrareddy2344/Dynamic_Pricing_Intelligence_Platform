import sys
import asyncio
import os

# Silence Playwright asyncio teardown exceptions on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if sys.platform == 'win32':
    # This must be set before any event loop is created.
    # Uvicorn on Windows defaults to SelectorEventLoop unless this is set here.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == '__main__':
    # Add project root to PYTHONPATH just to be extra sure
    project_root = os.path.abspath(os.path.dirname(__file__))
    
    # Add root and subdirectories to sys.path to handle dash/underscore packages
    paths_to_add = [
        project_root,
        os.path.join(project_root, "api_gateway"),
        os.path.join(project_root, "genai-service"),
        os.path.join(project_root, "ml-service"),
        os.path.join(project_root, "scraping-service"),
        os.path.join(project_root, "vision-service"),
    ]
    for p in paths_to_add:
        if p not in sys.path:
            sys.path.append(p)
    
    os.environ['PYTHONPATH'] = ";".join(paths_to_add) if sys.platform == 'win32' else ":".join(paths_to_add)
    
    print("Starting Synycs Dynamic Pricing API Gateway...")
    print("Using Proactor Event Loop for Windows/Playwright stability.")
    
    uvicorn.run(
        "api_gateway.main:app",
        host="127.0.0.1", 
        port=8000, 
        reload=True,
        log_level="info"
    )
