"""Standalone weekly newsletter sender.

Run by macOS launchd on a schedule so the digest arrives even when the web app
isn't running. Sends "what you saved this week", or — on a quiet week —
resurfaces a few older entries from the library.

    ./venv/bin/python send_digest.py
"""

from main import send_weekly_digest

if __name__ == "__main__":
    send_weekly_digest()
