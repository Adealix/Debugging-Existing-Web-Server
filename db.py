"""
db.py — MongoDB logger for Crayfish IoT System
-----------------------------------------------
Collections:
  • sensor_history   – analog sensor readings
  • actuator_history – digital actuator states
  • sms_events       – GSM / SMS actions

FIFO cap: only the latest MAX_HISTORY (10) documents are kept per collection.
Uses a module-level singleton so every import shares the same connection.
"""

import os
from pymongo import MongoClient, DESCENDING
from datetime import datetime

MAX_HISTORY = 10


class MongoLogger:
    def __init__(self, uri: str = None):
        self.client = None
        self.db = None
        self.sensor_col   = None
        self.actuator_col = None
        self.sms_col      = None
        self.col = None
        self._write_count = 0  # throttle _prune to every N writes

        if not uri:
            print("[MongoLogger] No URI provided — MongoDB disabled.")
            return

        try:
            self.client = MongoClient(uri, serverSelectionTimeoutMS=6000)
            self.client.admin.command("ping")
            self.db = self.client["crayfish_db"]

            self.sensor_col   = self.db["sensor_history"]
            self.actuator_col = self.db["actuator_history"]
            self.sms_col      = self.db["sms_events"]
            self.col          = self.sensor_col

            self._ensure_indexes()
            print("[MongoLogger] Connected to MongoDB Atlas.")
        except Exception as exc:
            print(f"[MongoLogger] Connection failed: {exc}")
            self.client = self.db = None
            self.sensor_col = self.actuator_col = self.sms_col = self.col = None

    def _ensure_indexes(self):
        for col in (self.sensor_col, self.actuator_col, self.sms_col):
            if col is not None:
                try:
                    col.create_index([("timestamp", DESCENDING)])
                except Exception:
                    pass

    def _prune(self, col):
        if col is None:
            return
        try:
            cutoff_docs = list(
                col.find({}, {"timestamp": 1})
                   .sort("timestamp", DESCENDING)
                   .skip(MAX_HISTORY)
                   .limit(1)
            )
            if cutoff_docs:
                cutoff_ts = cutoff_docs[0]["timestamp"]
                col.delete_many({"timestamp": {"$lte": cutoff_ts}})
        except Exception:
            pass

    def _prune_if_needed(self, col):
        self._write_count += 1
        if self._write_count % 10 == 0:
            self._prune(col)

    def insert_sensor(self, data: dict):
        if self.sensor_col is None:
            return
        doc = {
            "type":        "sensor",
            "timestamp":   datetime.utcnow(),
            "time":        data.get("time"),
            "ts":          data.get("ts"),
            "ph":          data.get("ph"),
            "temp":        data.get("temp"),
            "turbidity":   data.get("turbidity"),
            "light_lux":   data.get("light_lux"),
            "distance_cm": data.get("distance_cm"),
            "health":      data.get("health"),
        }
        try:
            self.sensor_col.insert_one(doc)
            self._prune_if_needed(self.sensor_col)
        except Exception as e:
            print(f"[MongoLogger] insert_sensor error: {e}")

    def insert_actuator(self, data: dict):
        if self.actuator_col is None:
            return
        doc = {
            "type":           "actuator",
            "timestamp":      datetime.utcnow(),
            "time":           data.get("time"),
            "ts":             data.get("ts"),
            "pump":           data.get("pump"),
            "peltier":        data.get("peltier"),
            "air_pump":       data.get("air_pump"),
            "filter_pump":    data.get("filter_pump"),
            "rgb":            data.get("rgb"),
            "rgb_color":      data.get("rgb_color"),
            "rgb_brightness": data.get("rgb_brightness"),
            "mode":           data.get("mode"),
        }
        try:
            self.actuator_col.insert_one(doc)
            self._prune_if_needed(self.actuator_col)
        except Exception as e:
            print(f"[MongoLogger] insert_actuator error: {e}")

    def insert_sms_event(self, status: str, detail: str = ""):
        if self.sms_col is None:
            return
        doc = {
            "type":      "sms",
            "timestamp": datetime.utcnow(),
            "status":    status,
            "detail":    detail,
        }
        try:
            self.sms_col.insert_one(doc)
            self._prune_if_needed(self.sms_col)
        except Exception as e:
            print(f"[MongoLogger] insert_sms_event error: {e}")

    def insert(self, data: dict):
        self.insert_sensor(data)
        self.insert_actuator(data)
        gsm = data.get("gsm_status")
        if gsm:
            self.insert_sms_event(status=gsm, detail=data.get("serial_raw", ""))

    def get_sensor_history(self, limit: int = MAX_HISTORY) -> list:
        if self.sensor_col is None:
            return []
        try:
            return list(
                self.sensor_col.find({"type": "sensor"})
                               .sort("timestamp", DESCENDING)
                               .limit(limit)
            )
        except Exception:
            return []

    def get_actuator_history(self, limit: int = MAX_HISTORY) -> list:
        if self.actuator_col is None:
            return []
        try:
            return list(
                self.actuator_col.find({"type": "actuator"})
                                 .sort("timestamp", DESCENDING)
                                 .limit(limit)
            )
        except Exception:
            return []

    def get_sms_history(self, limit: int = MAX_HISTORY) -> list:
        if self.sms_col is None:
            return []
        try:
            return list(
                self.sms_col.find({"type": "sms"})
                            .sort("timestamp", DESCENDING)
                            .limit(limit)
            )
        except Exception:
            return []

    def get_history(self, limit: int = MAX_HISTORY) -> list:
        return self.get_sensor_history(limit)

    def get_latest(self, limit: int = 1) -> list:
        return self.get_sensor_history(limit)


_DB_URI = os.getenv("DB_URI")
mongo_logger = MongoLogger(_DB_URI)
