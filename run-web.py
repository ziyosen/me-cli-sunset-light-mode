#!/usr/bin/env python3
"""Entry point for me-cli-sunset webui (FastAPI + uvicorn)."""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
os.chdir(PROJECT_DIR)
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv
load_dotenv()

import uvicorn

if __name__ == "__main__":
    host = os.getenv("WEBUI_HOST", "0.0.0.0")
    port = int(os.getenv("WEBUI_PORT", "8089"))
    uvicorn.run("webui.app:app", host=host, port=port, log_level="info", access_log=True)
