import sys

def log_debug(msg):
    # Print to both stdout and stderr to guarantee it shows up in the uvicorn terminal
    print(f"DEBUG_SCRAPER: {msg}")
    print(f"DEBUG_SCRAPER: {msg}", file=sys.stderr)
