#!/bin/bash
# Double-click to start your Knowledge Hub library locally, then open it in the browser.
# Close this window (or press Ctrl+C) to stop it.
cd "$(dirname "$0")" || exit 1
source venv/bin/activate
( sleep 2 && open "http://localhost:8000/app" ) &
exec uvicorn main:app --host 127.0.0.1 --port 8000
