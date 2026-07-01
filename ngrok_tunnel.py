import subprocess
import threading
import time
import re
import requests as req

from config import FLASK_PORT

NGROK_PORT = FLASK_PORT
_tunnel_url = None
_tunnel_lock = threading.Lock()


def get_tunnel_url():
    with _tunnel_lock:
        return _tunnel_url


def _set_tunnel_url(url):
    global _tunnel_url
    with _tunnel_lock:
        _tunnel_url = url


def _fetch_url_from_api(retries=10, delay=1.5):
    """Poll ngrok local API until tunnel URL is available."""
    for i in range(retries):
        try:
            res = req.get("http://127.0.0.1:4040/api/tunnels", timeout=3)
            data = res.json()
            tunnels = data.get("tunnels", [])
            for t in tunnels:
                public_url = t.get("public_url", "")
                if public_url.startswith("https://"):
                    return public_url
            # fallback: take first available
            if tunnels:
                return tunnels[0].get("public_url")
        except Exception:
            pass
        time.sleep(delay)
    return None


def _parse_url_from_output(line):
    """Fallback: parse ngrok URL directly from stdout."""
    match = re.search(r"https://[a-zA-Z0-9\-]+\.ngrok[^\s\"]+", line)
    return match.group(0) if match else None


def ngrok_worker():
    """Start ngrok and keep tunnel alive. Restarts on crash."""
    global _tunnel_url

    while True:
        print(f"[NGROK] Starting tunnel on port {NGROK_PORT}...")
        try:
            proc = subprocess.Popen(
                ["ngrok", "http", str(NGROK_PORT)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # Give ngrok a moment to boot, then fetch URL from its local API
            time.sleep(2)
            url = _fetch_url_from_api()

            if url:
                _set_tunnel_url(url)
                print(f"[NGROK] Tunnel active → {url}")
            else:
                print("[NGROK] Could not retrieve tunnel URL from API. Reading stdout...")

            # Keep reading stdout for URL fallback + crash detection
            for line in proc.stdout:
                line = line.strip()
                if line:
                    print(f"[NGROK] {line}")
                if not get_tunnel_url():
                    parsed = _parse_url_from_output(line)
                    if parsed:
                        _set_tunnel_url(parsed)
                        print(f"[NGROK] Tunnel active (from stdout) → {parsed}")

            proc.wait()
            print("[NGROK] Process exited. Restarting in 5s...")
            _set_tunnel_url(None)

        except FileNotFoundError:
            print("[NGROK] ERROR: 'ngrok' not found. Install it: https://ngrok.com/download")
            return  # Don't retry if ngrok isn't installed
        except Exception as e:
            print(f"[NGROK] Unexpected error: {e}")
            _set_tunnel_url(None)

        time.sleep(5)

