"""Run ShareBase: ``python -m sharebase``."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .app import create_app

load_dotenv()

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("SHAREBASE_HOST", "127.0.0.1")
    port = int(os.environ.get("SHAREBASE_PORT", "8080"))
    # Threaded so that an armed slow_response fault delays one request without
    # stalling the fault console used to disarm it.
    app.run(host=host, port=port, debug=False, threaded=True)
