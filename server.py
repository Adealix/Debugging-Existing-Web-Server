"""
server.py — Flask web server for Crayfish IoT Dashboard
---------------------------------------------------------
Endpoints:
  GET /              — status page
  GET /latest        — full current state + all history collections
  GET /history       — alias for /sensor_history (legacy compatibility)
  GET /sensor_history    — last 10 analog sensor readings
  GET /actuator_history  — last 10 actuator state snapshots
  GET /sms_history       — last 10 GSM / SMS events
"""

import os
import threading
from datetime import datetime

from flask import Flask, jsonify
from db import MongoLogger
from helpers import latest_snapshot

app = Flask(__name__)

DB_URI = os.getenv("DB_URI")
mongo_logger = MongoLogger(DB_URI)


# ──────────────────────────────────────────────────────────────────────────────
# Helper: serialise a MongoDB record list (stringify _id, isoformat timestamps)
# ──────────────────────────────────────────────────────────────────────────────

def _serialise(records: list) -> list:
    out = []
    for rec in records:
        rec = dict(rec)
        rec["_id"] = str(rec.get("_id", ""))
        if isinstance(rec.get("timestamp"), datetime):
            rec["timestamp"] = rec["timestamp"].isoformat()
        out.append(rec)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Background vision / sensor loop
# ──────────────────────────────────────────────────────────────────────────────

def vision_worker():
    """
    Runs the main sensor + camera loop in a daemon thread.
    Each yielded *data* dict from main_loop() is already persisted inside
    helpers.apply_serial_update() — no double-write here.
    """
    try:
        from app import main_loop
    except ImportError:
        return

    for data in main_loop():
        # apply_serial_update (called inside main_loop) handles all DB writes.
        # Nothing extra needed here.
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    db_status = "connected" if mongo_logger.sensor_col is not None else "offline"
    return f"""
    <h2>🦞 Crayfish IoT System</h2>
    <p>MongoDB: <strong>{db_status}</strong></p>
    <p>Endpoints:</p>
    <ul>
        <li><a href="/latest">/latest</a> — full live state</li>
        <li><a href="/sensor_history">/sensor_history</a> — last 10 sensor readings</li>
        <li><a href="/actuator_history">/actuator_history</a> — last 10 actuator states</li>
        <li><a href="/sms_history">/sms_history</a> — last 10 SMS / GSM events</li>
        <li><a href="/history">/history</a> — alias for /sensor_history</li>
    </ul>
    """


@app.route("/latest")
def route_latest():
    """Full current state including all three history collections."""
    return jsonify(latest_snapshot())


@app.route("/sensor_history")
def route_sensor_history():
    """Last 10 analog sensor readings (FIFO — oldest auto-dropped by db.py)."""
    records = mongo_logger.get_sensor_history(limit=10)
    return jsonify(_serialise(records))


@app.route("/actuator_history")
def route_actuator_history():
    """Last 10 actuator state snapshots."""
    records = mongo_logger.get_actuator_history(limit=10)
    return jsonify(_serialise(records))


@app.route("/sms_history")
def route_sms_history():
    """Last 10 SMS / GSM events (SENT, FAILED, IN_ACTION, RECEIVED …)."""
    records = mongo_logger.get_sms_history(limit=10)
    return jsonify(_serialise(records))


@app.route("/history")
def route_history():
    """Legacy alias — returns sensor history for backward compatibility."""
    records = mongo_logger.get_sensor_history(limit=10)
    return jsonify(_serialise(records))


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=vision_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000)  