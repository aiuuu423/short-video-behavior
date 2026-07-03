import json
import os
import time
from typing import Optional, List
from datetime import datetime
import config
from config import (
    LOCAL_CACHE_FILE,
    IDLE_THRESHOLD_MINUTES,
    PROCESSED_LINKS_FILE,
    FAILED_RECORDS_FILE,
    FIELD_START_TIME,
    FIELD_BEHAVIOR,
    FIELD_KEYWORD,
    FIELD_URL,
    FIELD_QUALIFIED,
    FIELD_FYF_DENSITY,
    FIELD_TASK,
    FIELD_SOURCE_LABEL,
    FIELD_BEHAVIOR_TAGS,
    FIELD_RECORDED_AT,
    FIELD_WRITE_STATUS,
    FIELD_DEDUPE_KEY,
)


class RecordManager:
    def __init__(self, bitable_client):
        self.bitable = bitable_client
        self.last_activity_time = None
        self._cache = self._load_cache()

    def _load_cache(self) -> dict:
        if os.path.exists(LOCAL_CACHE_FILE):
            try:
                with open(LOCAL_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_activity_time": None}

    def _save_cache(self):
        self._cache["last_activity_time"] = self.last_activity_time
        with open(LOCAL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

    def _now_timestamp_ms(self) -> int:
        return int(time.time() * 1000)

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _is_new_session(self) -> bool:
        if self.last_activity_time is None:
            return True
        idle_minutes = (time.time() - self.last_activity_time) / 60
        return idle_minutes >= IDLE_THRESHOLD_MINUTES

    def _update_activity(self):
        self.last_activity_time = time.time()
        self._save_cache()

    def _load_json_list(self, file_path: str) -> list:
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except Exception:
                return []
        return []

    def _save_json_list(self, file_path: str, data: list):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _dedupe_key(self, video_url: str, video_id: Optional[str]) -> str:
        return video_id or video_url.strip()

    def _mark_processed(self, dedupe_key: str, video_url: str, task_name: str, source_label: str, record_id: str = ""):
        processed = self._load_json_list(PROCESSED_LINKS_FILE)
        processed.append({
            "dedupe_key": dedupe_key,
            "video_url": video_url,
            "record_id": record_id,
            "recorded_at": self._now_str(),
            "task_name": task_name,
            "source_label": source_label,
        })
        self._save_json_list(PROCESSED_LINKS_FILE, processed[-1000:])

    def _find_processed(self, dedupe_key: str) -> Optional[dict]:
        for item in self._load_json_list(PROCESSED_LINKS_FILE):
            if item.get("dedupe_key") == dedupe_key:
                return item
        return None

    def _save_failed(self, fields: dict, error_message: str, kind: str):
        failed = self._load_json_list(FAILED_RECORDS_FILE)
        failed.append({
            "kind": kind,
            "fields": fields,
            "error_message": error_message,
            "retry_count": 0,
            "failed_at": self._now_str(),
        })
        self._save_json_list(FAILED_RECORDS_FILE, failed)

    def failed_count(self) -> int:
        return len(self._load_json_list(FAILED_RECORDS_FILE))

    def record_feed_video(
        self,
        video_url: str,
        video_id: Optional[str],
        is_violation: bool,
        task_name: str = "",
        source_label: str = "",
        behavior_tags: Optional[List[str]] = None,
        cache_on_fail: bool = True,
    ) -> dict:
        behavior_tags = behavior_tags or ["复制链接"]
        dedupe_key = self._dedupe_key(video_url, video_id)

        fields = {
            FIELD_BEHAVIOR: "刷feed",
            FIELD_KEYWORD: "",
            FIELD_URL: video_url,
            FIELD_QUALIFIED: "是" if is_violation else "否",
            FIELD_FYF_DENSITY: "",
            FIELD_TASK: task_name,
            FIELD_SOURCE_LABEL: source_label,
            FIELD_BEHAVIOR_TAGS: behavior_tags,
            FIELD_RECORDED_AT: self._now_timestamp_ms(),
            FIELD_WRITE_STATUS: "成功",
            FIELD_DEDUPE_KEY: dedupe_key,
        }
        if self._is_new_session():
            fields[FIELD_START_TIME] = self._now_timestamp_ms()
        try:
            record = self.bitable.create_record(fields)
            record_id = record.get("record_id", "")
            self._mark_processed(dedupe_key, video_url, task_name, source_label, record_id)
            self._update_activity()
            return {"action": "appended", "url": video_url, "record_id": record_id, "is_violation": is_violation}
        except Exception as e:
            if cache_on_fail:
                self._save_failed(fields, str(e), "feed")
            return {"action": "failed_cached", "error": str(e), "failed_count": self.failed_count()}

    def mark_record_violation(self, record_id: str) -> dict:
        if not record_id:
            return {"action": "failed", "error": "缺少 record_id，无法修正上一条记录"}
        try:
            self.bitable.update_record(record_id, {FIELD_QUALIFIED: "是"})
            self._update_activity()
            return {"action": "updated", "record_id": record_id, "field": FIELD_QUALIFIED, "value": "是"}
        except Exception as e:
            return {"action": "failed", "error": str(e)}

    def record_search(self, keyword: str, task_name: str = "", source_label: str = "") -> dict:
        fields = {
            FIELD_BEHAVIOR: "搜索词",
            FIELD_KEYWORD: keyword,
            FIELD_URL: "",
            FIELD_QUALIFIED: "",
            FIELD_FYF_DENSITY: "",
            FIELD_TASK: task_name,
            FIELD_SOURCE_LABEL: source_label,
            FIELD_BEHAVIOR_TAGS: ["搜索"],
            FIELD_RECORDED_AT: self._now_timestamp_ms(),
            FIELD_WRITE_STATUS: "成功",
        }
        if self._is_new_session():
            fields[FIELD_START_TIME] = self._now_timestamp_ms()
        try:
            self.bitable.create_record(fields)
            self._update_activity()
            return {"action": "appended_search", "keyword": keyword}
        except Exception as e:
            self._save_failed(fields, str(e), "search")
            return {"action": "failed_cached", "error": str(e), "failed_count": self.failed_count()}

    def flush_pending(self):
        failed = self._load_json_list(FAILED_RECORDS_FILE)
        if not failed:
            return {"retried": 0, "succeeded": 0, "remaining": 0}

        remaining = []
        succeeded = 0
        for item in failed:
            fields = item.get("fields", {})
            try:
                self.bitable.create_record(fields)
                succeeded += 1
            except Exception as e:
                item["retry_count"] = int(item.get("retry_count", 0)) + 1
                item["error_message"] = str(e)
                remaining.append(item)
        self._save_json_list(FAILED_RECORDS_FILE, remaining)
        return {"retried": len(failed), "succeeded": succeeded, "remaining": len(remaining)}
