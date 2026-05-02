"""
app.py — Hugging Face Spaces entry point
=========================================
Hugging Face Spaces (Docker SDK) runs this file.
It simply starts the FastAPI server on port 7860,
which is the port HF Spaces exposes publicly for free.

Live URL will be: https://huggingface.co/spaces/Chege-N/TDA_Supply_Chain
"""

import os
import sys

# Make sure src/ is importable
sys.path.insert(0, os.path.dirname(__file__))

import uvicorn
from src.api.rest_api import app  # the FastAPI app object

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )
