#!/usr/bin/env python3
"""Run the bundled standalone Hermes internal mail queue processor."""
import runpy
from pathlib import Path

PROCESSOR = Path(__file__).resolve().parents[2] / "backend" / "app" / "assets" / "process_internal_mail_queue.py"

if __name__ == "__main__":
    runpy.run_path(str(PROCESSOR), run_name="__main__")
