import sys
import os
import time
import threading
import re
import warnings
import select
import termios
import tty
import queue
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pyperclip

warnings.filterwarnings("ignore", category=Warning, module="urllib3")

import config
from config import POLL_INTERVAL
from tiktok_parser import parse_video_link

DEFAULT_FEISHU_APP_ID = os.getenv("DEFAULT_FEISHU_APP_ID", "")
DEFAULT_FEISHU_APP_SECRET = os.getenv("DEFAULT_FEISHU_APP_SECRET", "")
CAPTURE_LOG_FILE = os.path.join("data", "captured_links.jsonl")
WRITE_WORKER_COUNT = 2
WRITE_MAX_RETRIES = 3
VIOLATION_MARKERS = {"违规", "0", "TT_VIOLATION_NEXT", "TT违规"}
VIOLATION_MARK_WINDOW_SECONDS = 10
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8765
RECENT_DUPLICATE_WINDOW_SECONDS = 20
METADATA_MARK_WINDOW_SECONDS = 60
POLICY_HOTKEYS = {
    "1": "EDF",
    "2": "NSA",
    "3": "SSH",
    "4": "HHB",
}
INTEREST_HOTKEYS = {
    "f": "符合",
    "n": "不符合",
}
POLICY_ALIASES = {
    "edf": "EDF",
    "nsa": "NSA",
    "ssh": "SSH",
    "hhb": "HHB",
}
INTEREST_ALIASES = {
    "1": "符合",
    "yes": "符合",
    "y": "符合",
    "true": "符合",
    "fit": "符合",
    "符合": "符合",
    "0": "不符合",
    "no": "不符合",
    "n": "不符合",
    "false": "不符合",
    "unfit": "不符合",
    "不符合": "不符合",
}


def parse_bitable_url(value: str) -> Tuple[str, str]:
    app_token = ""
    table_id = ""
    token_match = re.search(r"/base/([^?/#]+)", value)
    table_match = re.search(r"[?&]table=([^&#]+)", value)
    if token_match:
        app_token = token_match.group(1)
    if table_match:
        table_id = table_match.group(1)
    return app_token, table_id


def append_capture_log(item: dict):
    os.makedirs(os.path.dirname(CAPTURE_LOG_FILE), exist_ok=True)
    with open(CAPTURE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def is_violation_marker(text: str) -> bool:
    return text.strip() in VIOLATION_MARKERS


def notify_beep(times: int = 1):
    def _beep():
        for _ in range(times):
            sys.stdout.write("\a")
            sys.stdout.flush()
            time.sleep(0.05)
    threading.Thread(target=_beep, daemon=True).start()


def normalize_policy(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return POLICY_ALIASES.get(raw.lower(), raw.upper())


def normalize_interest_profile(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return INTEREST_ALIASES.get(raw.lower(), raw)


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class ClipboardMonitor:
    def __init__(self, record_manager, task_name: str, source_label: str, target_name: str):
        self.manager = record_manager
        self.task_name = task_name
        self.source_label = source_label
        self.target_name = target_name
        self._last_text = ""
        self._running = False
        self._thread = None
        self._old_terminal_settings = None
        self._pending_violation = None
        self._last_record = None
        self._state_lock = threading.Lock()
        self._pending_seq = 0
        self._record_queue = queue.Queue()
        self._items = {}
        self._worker_threads = []
        self._http_server = None
        self._http_thread = None
        self._recent_urls = {}
        self._next_link_violation = False

    def _poll_loop(self):
        self._last_text = pyperclip.paste()
        self._enable_single_key_mode()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Agent 已启动，正在监听剪贴板。")
        print(f"当前配置：采集任务={self.task_name}，来源标签={self.source_label}，飞书写入目标={self.target_name}")
        print("最终模式：不违规只复制链接；违规复制链接后点一次悬浮球；按 q 退出。")
        print("快捷标注：v/0=违规，1=EDF，2=NSA，3=SSH，4=HHB，f=兴趣画像符合，n=兴趣画像不符合。")
        try:
            while self._running:
                try:
                    current = pyperclip.paste()
                    if current != self._last_text:
                        self._last_text = current
                        self._handle_clipboard_change(current)
                    self._handle_keypress()
                    self._clear_expired_pending()
                except Exception as e:
                    print(f"剪贴板读取错误: {e}")
                time.sleep(0.2)
        finally:
            self._disable_single_key_mode()

    def _enable_single_key_mode(self):
        if not sys.stdin.isatty():
            return
        try:
            self._old_terminal_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        except Exception:
            self._old_terminal_settings = None

    def _disable_single_key_mode(self):
        if self._old_terminal_settings is None:
            return
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_terminal_settings)
        except Exception:
            pass
        self._old_terminal_settings = None

    def _read_key(self) -> Optional[str]:
        if not sys.stdin.isatty():
            return None
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        if not readable:
            return None
        return sys.stdin.read(1)

    def _handle_keypress(self):
        key = self._read_key()
        if not key:
            return
        key = key.lower()
        if key in ("q", "\x03"):
            print("\n正在退出监听。")
            self.stop()
            return
        if key in ("v", "0"):
            self._mark_pending_violation()
            return
        if key in POLICY_HOTKEYS:
            self._mark_pending_metadata(policy=POLICY_HOTKEYS[key])
            return
        if key in INTEREST_HOTKEYS:
            self._mark_pending_metadata(interest_profile=INTEREST_HOTKEYS[key])
            return
        if key in ("9", "s"):
            print("\n快捷键：v/0=违规，1=EDF，2=NSA，3=SSH，4=HHB，f=兴趣画像符合，n=兴趣画像不符合，q=退出。")

    def _clear_expired_pending(self):
        with self._state_lock:
            if self._pending_violation and time.time() > self._pending_violation["deadline"]:
                self._pending_violation = None

    def _handle_clipboard_change(self, text: str):
        if is_violation_marker(text):
            notify_beep(1)
            self._mark_pending_violation()
            return

        parsed = parse_video_link(text)
        if not parsed:
            return
        self._enqueue_parsed_link(parsed, is_violation=False, capture_source="clipboard")

    def submit_external_link(
        self,
        text: str,
        is_violation: bool,
        capture_source: str = "http",
        policy: str = "",
        interest_profile: str = "",
    ) -> dict:
        parsed = parse_video_link(text)
        if not parsed:
            return {"ok": False, "error": "未识别到受支持平台的视频链接"}
        seq = self._enqueue_parsed_link(
            parsed,
            is_violation=is_violation,
            capture_source=capture_source,
            policy=policy,
            interest_profile=interest_profile,
        )
        return {
            "ok": True,
            "seq": seq,
            "is_violation": is_violation,
            "policy": policy,
            "interest_profile": interest_profile,
        }

    def _enqueue_parsed_link(
        self,
        parsed: dict,
        is_violation: bool,
        capture_source: str,
        policy: str = "",
        interest_profile: str = "",
    ) -> int:
        now = time.time()
        duplicate_seq = None
        duplicate_update = None
        with self._state_lock:
            for url, recent in list(self._recent_urls.items()):
                if now - recent["time"] > RECENT_DUPLICATE_WINDOW_SECONDS:
                    self._recent_urls.pop(url, None)

            recent = self._recent_urls.get(parsed["normalized_url"])
            if recent and now - recent["time"] <= RECENT_DUPLICATE_WINDOW_SECONDS:
                duplicate_seq = recent["seq"]
                item = self._items.get(duplicate_seq)
                if item:
                    if policy:
                        item["policy"] = policy
                    if interest_profile:
                        item["interest_profile"] = interest_profile
                    if policy or interest_profile:
                        duplicate_update = {
                            "record_id": item.get("record_id", ""),
                            "policy": item.get("policy", ""),
                            "interest_profile": item.get("interest_profile", ""),
                        }
                    if is_violation and not item.get("violation_requested"):
                        self._pending_violation = item

        if duplicate_seq is not None:
            if is_violation:
                print(f"\n#{duplicate_seq} ✅ 收到同一链接的违规直传，正在升级为“违规”")
                self._mark_pending_violation()
            if duplicate_update and duplicate_update.get("record_id"):
                self._async_update_labels(
                    duplicate_update["record_id"],
                    duplicate_seq,
                    policy=duplicate_update.get("policy", ""),
                    interest_profile=duplicate_update.get("interest_profile", ""),
                )
            elif policy or interest_profile:
                labels = []
                if policy:
                    labels.append(f"policy={policy}")
                if interest_profile:
                    labels.append(f"兴趣画像={interest_profile}")
                print(f"\n#{duplicate_seq} ✅ 已收到补充标注：{', '.join(labels)}")
            elif not is_violation:
                print(f"\n#{duplicate_seq} 已收到同一链接，跳过重复写入")
            return duplicate_seq

        with self._state_lock:
            self._pending_seq += 1
            seq = self._pending_seq
            item = {
                "seq": seq,
                "record_id": "",
                "url": parsed["normalized_url"],
                "video_id": parsed["video_id"],
                "deadline": time.time() + VIOLATION_MARK_WINDOW_SECONDS,
                "violation_requested": is_violation,
                "policy": policy,
                "interest_profile": interest_profile,
                "status": "queued",
            }
            self._items[seq] = item
            self._pending_violation = item
            self._recent_urls[parsed["normalized_url"]] = {"seq": seq, "time": now}
        platform = parsed.get("platform") or "未知平台"
        print(f"\n#{seq} [{datetime.now().strftime('%H:%M:%S')}] 检测到视频链接（{platform}）:")
        print(f"#{seq} 视频ID: {parsed['video_id'] or '未知'}")
        print(f"#{seq} 链接: {parsed['normalized_url']}")
        print(f"#{seq} 判定: {'违规' if is_violation else '不违规'}")
        if policy:
            print(f"#{seq} policy: {policy}")
        if interest_profile:
            print(f"#{seq} 兴趣画像: {interest_profile}")
        print(f"#{seq} 来源: {capture_source}")
        append_capture_log({
            "seq": seq,
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "url": parsed["normalized_url"],
            "video_id": parsed["video_id"],
            "platform": parsed.get("platform"),
            "is_violation": is_violation,
            "policy": policy,
            "interest_profile": interest_profile,
            "capture_source": capture_source,
            "task_name": self.task_name,
            "source_label": self.source_label,
            "target_name": self.target_name,
        })
        self._record_queue.put(seq)
        print(f"#{seq} ✅ 已加入写入队列（队列剩余 {self._record_queue.qsize()}）")
        notify_beep(1)
        return seq

    def _record_worker_loop(self):
        while self._running or not self._record_queue.empty():
            try:
                seq = self._record_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._state_lock:
                item = self._items.get(seq)
                if not item:
                    self._record_queue.task_done()
                    continue
                item["status"] = "writing"
                write_as_violation = bool(item.get("violation_requested"))
                write_policy = item.get("policy", "")
                write_interest_profile = item.get("interest_profile", "")
            try:
                result = None
                for attempt in range(1, WRITE_MAX_RETRIES + 1):
                    result = self.manager.record_feed_video(
                        video_url=item["url"],
                        video_id=item.get("video_id"),
                        is_violation=write_as_violation,
                        task_name=self.task_name,
                        source_label=self.source_label,
                        policy=write_policy,
                        interest_profile=write_interest_profile,
                        cache_on_fail=(attempt == WRITE_MAX_RETRIES),
                    )
                    if result.get("action") != "failed_cached":
                        break
                    with self._state_lock:
                        item["status"] = "retrying"
                        item["last_error"] = result.get("error", "")
                    if attempt == WRITE_MAX_RETRIES:
                        break
                    print(f"#{seq} 写入失败，自动重试 {attempt}/{WRITE_MAX_RETRIES}: {result.get('error')}")
                    time.sleep(min(attempt, 2))
                if result.get("action") in ("appended", "updated"):
                    record_id = result.get("record_id", "")
                    update_after_write = False
                    with self._state_lock:
                        item["record_id"] = record_id
                        item["status"] = "done"
                        self._last_record = {
                            "record_id": record_id,
                            "url": item["url"],
                            "seq": seq,
                            "created_at": time.time(),
                            "policy": item.get("policy", ""),
                            "interest_profile": item.get("interest_profile", ""),
                        }
                        if self._pending_violation and self._pending_violation.get("seq") == seq:
                            self._pending_violation["record_id"] = record_id
                        update_after_write = bool(item.get("violation_requested")) and not write_as_violation
                        labels_after_write = {
                            "policy": item.get("policy", ""),
                            "interest_profile": item.get("interest_profile", ""),
                        }
                    if update_after_write:
                        mark_result = self.manager.mark_record_violation(record_id)
                        if mark_result.get("action") == "updated":
                            print(f"#{seq} ✅ 已写入：违规")
                        else:
                            print(f"\n#{seq} 修正失败: {mark_result.get('error')}")
                    late_label_update = (
                        record_id
                        and (
                            labels_after_write.get("policy") != write_policy
                            or labels_after_write.get("interest_profile") != write_interest_profile
                        )
                    )
                    if late_label_update:
                        self._async_update_labels(
                            record_id,
                            seq,
                            policy=labels_after_write.get("policy", ""),
                            interest_profile=labels_after_write.get("interest_profile", ""),
                        )
                    if not update_after_write:
                        self._print_result(result, seq)
                else:
                    self._print_result(result, seq)
                    with self._state_lock:
                        item["status"] = "failed" if result.get("action") == "failed_cached" else "done"
            except Exception as e:
                print(f"#{seq} 写入失败: {e}")
                with self._state_lock:
                    item["status"] = "failed"
            finally:
                self._record_queue.task_done()

    def _async_update_labels(
        self,
        record_id: str,
        seq,
        policy: str = "",
        interest_profile: str = "",
    ):
        def _run():
            result = self.manager.update_record_labels(
                record_id,
                policy=policy,
                interest_profile=interest_profile,
            )
            if result.get("action") == "updated":
                labels = []
                if policy:
                    labels.append(f"policy={policy}")
                if interest_profile:
                    labels.append(f"兴趣画像={interest_profile}")
                print(f"#{seq} ✅ 已更新标注：{', '.join(labels)}")
            elif result.get("action") != "skipped":
                print(f"\n#{seq} 标注更新失败: {result.get('error')}")

        threading.Thread(target=_run, daemon=True).start()

    def _pick_metadata_target_locked(self):
        now = time.time()
        pending = self._pending_violation
        if pending and now <= pending.get("deadline", 0):
            return pending
        if self._last_record and now - self._last_record.get("created_at", 0) <= METADATA_MARK_WINDOW_SECONDS:
            return self._last_record
        return None

    def _mark_pending_metadata(self, policy: str = "", interest_profile: str = ""):
        policy = normalize_policy(policy)
        interest_profile = normalize_interest_profile(interest_profile)
        with self._state_lock:
            target = self._pick_metadata_target_locked()
            if not target:
                print("\n⚠️ 没有可标注的最近链接：请先复制视频链接，再按标注快捷键。")
                return
            if policy:
                target["policy"] = policy
            if interest_profile:
                target["interest_profile"] = interest_profile
            record_id = target.get("record_id", "")
            seq = target.get("seq", "?")
            status = target.get("status")

        labels = []
        if policy:
            labels.append(f"policy={policy}")
        if interest_profile:
            labels.append(f"兴趣画像={interest_profile}")

        if not record_id:
            print(f"\n#{seq} ✅ 已收到标注：{', '.join(labels)}，将随该链接一起写入")
            return
        if status and status != "done":
            print(f"\n#{seq} ✅ 已收到标注：{', '.join(labels)}，等待写入完成后补写")
            return
        self._async_update_labels(
            record_id,
            seq,
            policy=policy,
            interest_profile=interest_profile,
        )

    def mark_external(self, is_violation: bool = False, policy: str = "", interest_profile: str = "") -> dict:
        policy = normalize_policy(policy)
        interest_profile = normalize_interest_profile(interest_profile)
        if policy or interest_profile:
            self._mark_pending_metadata(policy=policy, interest_profile=interest_profile)
        if is_violation:
            self._mark_pending_violation()
        return {
            "ok": True,
            "is_violation": is_violation,
            "policy": policy,
            "interest_profile": interest_profile,
        }

    @staticmethod
    def _panel_html() -> str:
        return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>短视频标注面板</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#f7f7f5;color:#222}
main{max-width:520px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 16px}
.group{margin:18px 0}
.label{font-size:13px;color:#666;margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
button{height:54px;border:1px solid #d7d3ca;background:#fff;border-radius:8px;font-size:18px;font-weight:650;color:#222}
button:active{background:#e9f2ff}
.danger{background:#fff2f0;border-color:#ffc7bd}
.status{margin-top:14px;font-size:14px;color:#555;min-height:22px}
</style>
</head>
<body>
<main>
<h1>最近一条视频标注</h1>
<div class="group">
<div class="label">policy</div>
<div class="grid">
<button onclick="mark('policy=EDF')">EDF</button>
<button onclick="mark('policy=NSA')">NSA</button>
<button onclick="mark('policy=SSH')">SSH</button>
<button onclick="mark('policy=HHB')">HHB</button>
</div>
</div>
<div class="group">
<div class="label">兴趣画像</div>
<div class="grid">
<button onclick="mark('interest=符合')">符合</button>
<button onclick="mark('interest=不符合')">不符合</button>
</div>
</div>
<div class="group">
<button class="danger" style="width:100%" onclick="mark('violation=1')">标记违规</button>
</div>
<div id="status" class="status"></div>
</main>
<script>
async function mark(q){
  const el=document.getElementById('status');
  el.textContent='写入中...';
  try{
    const r=await fetch('/mark?'+q);
    const data=await r.json();
    el.textContent=data.ok?'已发送':'失败：'+(data.error||'unknown');
  }catch(e){el.textContent='失败：'+e.message}
}
</script>
</body>
</html>"""

    def _mark_pending_violation(self):
        with self._state_lock:
            pending = self._pending_violation
            if not pending or time.time() > pending["deadline"]:
                self._pending_violation = None
                print("\n⚠️ 没有可标记的最近链接：请先复制视频链接，再点一次悬浮球。")
                return
            if pending.get("violation_requested"):
                print(f"\n#{pending.get('seq')} 已经标记为违规，无需重复操作。")
                return
            pending["violation_requested"] = True
            record_id = pending.get("record_id", "")
            status = pending.get("status")
            seq = pending.get("seq")
        if not record_id:
            print(f"\n#{seq} ✅ 已收到违规标记：这条链接将按“违规”写入")
            return
        if status != "done":
            print(f"\n#{seq} ✅ 已收到违规标记：正在等待写入完成后修正为“违规”")
            return
        
        def _async_mark():
            result = self.manager.mark_record_violation(record_id)
            if result.get("action") == "updated":
                print(f"#{seq} ✅ 已将当前记录改为违规")
                with self._state_lock:
                    if self._pending_violation and self._pending_violation.get("record_id") == record_id:
                        self._pending_violation = None
            else:
                print(f"\n#{seq} 修正失败: {result.get('error')}")
                
        threading.Thread(target=_async_mark, daemon=True).start()

    def _mark_last_violation(self):
        if not self._last_record:
            print("\n还没有上一条记录，无法修正。")
            return
            
        record_id = self._last_record.get("record_id", "")
        seq = self._last_record.get("seq", "?")
        
        def _async_mark():
            result = self.manager.mark_record_violation(record_id)
            if result.get("action") == "updated":
                print(f"#{seq} ✅ 已将上一条记录修正为违规")
            else:
                print(f"\n#{seq} 修正失败: {result.get('error')}")
                
        threading.Thread(target=_async_mark, daemon=True).start()

    def _print_result(self, result: dict, seq=None):
        prefix = f"#{seq} " if seq is not None else ""
        action = result.get("action")
        if action == "appended":
            status = "违规" if result.get("is_violation") else "不违规"
            print(f"{prefix}✅ 已写入：{status}")
        elif action == "updated":
            print(f"{prefix}✅ 已更新为：违规")
        elif action == "skipped":
            reason = result.get("reason", "")
            if reason == "already_qualified":
                print(f"{prefix}⏭️ 已存在且为违规，跳过")
            elif reason == "already_processed":
                last = result.get("last_record", {})
                print(f"{prefix}⏭️ 该视频链接已经记录过，本次不重复写入。")
                print(f"{prefix}上次记录时间: {last.get('recorded_at', '未知')}")
            else:
                print(f"{prefix}⏭️ 已存在且为不违规，跳过")
        elif action == "failed_cached":
            print(f"{prefix}⚠️ 写入飞书失败，记录已保存到本地失败队列。")
            print(f"{prefix}待重试记录数: {result.get('failed_count')}")

    def start(self):
        self._running = True
        self._worker_threads = [t for t in self._worker_threads if t.is_alive()]
        if not self._start_http_server():
            self._running = False
            return False
        while len(self._worker_threads) < WRITE_WORKER_COUNT:
            worker = threading.Thread(target=self._record_worker_loop, daemon=True)
            worker.start()
            self._worker_threads.append(worker)
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._http_server:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._thread and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)

    def _start_http_server(self):
        if self._http_server:
            return True
        monitor = self

        class RecordHandler(BaseHTTPRequestHandler):
            def _send_json(self, status: int, payload: dict):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, status: int, html: str):
                body = html.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed_url = urlparse(self.path)
                if parsed_url.path not in ("/record", "/health", "/mark", "/panel"):
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                if parsed_url.path == "/panel":
                    self._send_html(200, monitor._panel_html())
                    return
                if parsed_url.path == "/health":
                    self._send_json(200, {"ok": True})
                    return
                params = parse_qs(parsed_url.query)
                if parsed_url.path == "/mark":
                    violation_raw = (params.get("violation") or params.get("is_violation") or ["0"])[0]
                    is_violation = str(violation_raw).lower() in ("1", "true", "yes", "y", "是", "违规")
                    policy = normalize_policy((params.get("policy") or params.get("issue") or [""])[0])
                    interest_profile = normalize_interest_profile(
                        (
                            params.get("interest")
                            or params.get("interest_profile")
                            or params.get("profile")
                            or params.get("兴趣画像")
                            or [""]
                        )[0]
                    )
                    result = monitor.mark_external(
                        is_violation=is_violation,
                        policy=policy,
                        interest_profile=interest_profile,
                    )
                    self._send_json(200, result)
                    return
                link = (params.get("url") or params.get("link") or [""])[0]
                violation_raw = (params.get("violation") or params.get("is_violation") or ["0"])[0]
                is_violation = str(violation_raw).lower() in ("1", "true", "yes", "y", "是", "违规")
                policy = normalize_policy((params.get("policy") or params.get("issue") or [""])[0])
                interest_profile = normalize_interest_profile(
                    (
                        params.get("interest")
                        or params.get("interest_profile")
                        or params.get("profile")
                        or params.get("兴趣画像")
                        or [""]
                    )[0]
                )
                result = monitor.submit_external_link(
                    link,
                    is_violation,
                    "iphone-http",
                    policy=policy,
                    interest_profile=interest_profile,
                )
                self._send_json(200 if result.get("ok") else 400, result)

            def do_POST(self):
                if urlparse(self.path).path != "/record":
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                try:
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = {}
                link = payload.get("url") or payload.get("link") or ""
                is_violation = bool(payload.get("violation") or payload.get("is_violation"))
                policy = normalize_policy(payload.get("policy") or payload.get("issue") or "")
                interest_profile = normalize_interest_profile(
                    payload.get("interest")
                    or payload.get("interest_profile")
                    or payload.get("profile")
                    or payload.get("兴趣画像")
                    or ""
                )
                result = monitor.submit_external_link(
                    link,
                    is_violation,
                    "iphone-http",
                    policy=policy,
                    interest_profile=interest_profile,
                )
                self._send_json(200 if result.get("ok") else 400, result)

            def log_message(self, format, *args):
                return

        try:
            self._http_server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), RecordHandler)
        except OSError as e:
            print(f"⚠️ HTTP 直传服务启动失败：{e}")
            print("检测到可能已有一个 Agent 正在运行。为避免重复录入，本次启动已停止。")
            return False
        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        lan_ip = get_lan_ip()
        print(f"HTTP 直传已开启：http://{lan_ip}:{HTTP_PORT}/record")
        print("iPhone 快捷指令可直接请求该地址，绕过 Apple 跨设备剪贴板。")
        return True


def startup_flush_async(manager):
    """启动后在后台补写历史失败记录，不阻塞剪贴板监听。"""
    def _run():
        time.sleep(1)
        try:
            result = manager.flush_pending()
        except Exception as e:
            print(f"\n📦 历史失败记录补写异常: {e}")
            return
        if result.get("retried"):
            print(
                f"\n📦 历史失败记录补写完成：成功 {result['succeeded']} 条，"
                f"已存在跳过 {result['skipped_existing']} 条，仍失败 {result['remaining']} 条。"
            )

    threading.Thread(target=_run, daemon=True).start()


def run_monitor(monitor: ClipboardMonitor):
    if not monitor.start():
        return
    startup_flush_async(monitor.manager)
    print("【不违规】只复制视频链接（TikTok/YouTube/Instagram/Facebook），自动按不违规写入。")
    print("【违规】先复制视频链接，再点一次悬浮球，自动按违规写入/修正。按 q 退出。")
    print("【补充标注】终端按 1/2/3/4 选 policy，按 f/n 选兴趣画像；HTTP 可加 policy=NSA&interest=符合。")
    while monitor._running:
        time.sleep(0.2)
    if not monitor._record_queue.empty():
        print("正在完成剩余写入，请稍等...")
        monitor._record_queue.join()
    print("\n已退出。")


def main():
    print("\n" + "=" * 58)
    print("短视频行为记录 Agent")
    print("=" * 58)
    
    # 尝试加载上次的配置
    accounts = config.list_feishu_targets()
    target_name = "default"
    
    if accounts:
        target_name = accounts[0]["name"]
        config.load_account(target_name)
        target_type = config.get_env("FEISHU_TARGET_TYPE", "bitable").lower()
        app_token = config.get_env("BITABLE_APP_TOKEN")
        table_id = config.get_env("BITABLE_TABLE_ID")
        if target_type == "sheet":
            sheet_id = config.get_env("SHEET_ID") or config.get_env("SHEET_NAME")
            print(f"✅ 已自动加载上次配置「{target_name}」 (Sheet: {sheet_id})")
        else:
            print(f"✅ 已自动加载上次配置「{target_name}」 (Table ID: {table_id})")
    else:
        while True:
            try:
                target_name = input("首次运行，请输入配置名称（如：张三的养号表）: ").strip()
                if not target_name:
                    continue
                
                bitable_input = input("请粘贴飞书多维表格链接（直接粘贴完整 URL 回车）: ").strip()
                if not bitable_input:
                    continue
                app_token, table_id = parse_bitable_url(bitable_input)
                if not app_token or not table_id:
                    print("❌ 无法从链接中识别 App Token 或 Table ID，请确保粘贴的是完整的多维表格链接。")
                    continue
                app_id = DEFAULT_FEISHU_APP_ID or input("请输入飞书 App ID: ").strip()
                app_secret = DEFAULT_FEISHU_APP_SECRET or input("请输入飞书 App Secret: ").strip()
                if not app_id or not app_secret:
                    print("❌ 飞书 App ID 和 App Secret 不能为空。")
                    continue
                
                config.create_account(
                    target_name, 
                    app_id, 
                    app_secret, 
                    app_token, 
                    table_id
                )
                config.load_account(target_name)
                print(f"✅ 飞书配置「{target_name}」保存成功！")
                break
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                return

    from record_manager import RecordManager

    try:
        target_type = config.get_env("FEISHU_TARGET_TYPE", "bitable").lower()
        if target_type == "sheet":
            from feishu_sheet import FeishuSheetClient

            feishu_client = FeishuSheetClient()
        else:
            from feishu_bitable import FeishuBitableClient

            feishu_client = FeishuBitableClient()
    except Exception as e:
        print(f"初始化飞书写入客户端失败: {e}")
        return

    manager = RecordManager(feishu_client)
    
    # 使用固定或继承的名称，不再繁琐询问
    task_name = "自动记录任务"
    source_label = target_name
    
    monitor = ClipboardMonitor(manager, task_name, source_label, target_name)
    run_monitor(monitor)


if __name__ == "__main__":
    main()
