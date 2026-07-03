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
from tiktok_parser import parse_tiktok_link

DEFAULT_FEISHU_APP_ID = os.getenv("DEFAULT_FEISHU_APP_ID", "")
DEFAULT_FEISHU_APP_SECRET = os.getenv("DEFAULT_FEISHU_APP_SECRET", "")
CAPTURE_LOG_FILE = os.path.join("data", "captured_links.jsonl")
WRITE_WORKER_COUNT = 1
WRITE_MAX_RETRIES = 5
VIOLATION_MARKERS = {"违规", "0", "TT_VIOLATION_NEXT", "TT违规"}
VIOLATION_MARK_WINDOW_SECONDS = 10
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8765
RECENT_DUPLICATE_WINDOW_SECONDS = 20


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
    for _ in range(times):
        sys.stdout.write("\a")
        sys.stdout.flush()
        time.sleep(0.05)


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
        print(f"当前配置：采集任务={self.task_name}，TikTok 来源标签={self.source_label}，飞书写入目标={self.target_name}")
        print("推荐模式：iPhone 快捷指令直传链接到 Mac；剪贴板监听仅作为备用；按 q 退出。")
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
                time.sleep(0.01)
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
        if key in ("0", "9", "s"):
            print("\n提示：当前是手机标记模式，不需要在电脑按键标注；按 q 可退出监听。")

    def _clear_expired_pending(self):
        with self._state_lock:
            if self._pending_violation and time.time() > self._pending_violation["deadline"]:
                self._pending_violation = None

    def _handle_clipboard_change(self, text: str):
        if is_violation_marker(text):
            notify_beep(1)
            self._mark_pending_violation()
            return

        parsed = parse_tiktok_link(text)
        if not parsed:
            return
        self._enqueue_parsed_link(parsed, is_violation=False, capture_source="clipboard")

    def submit_external_link(self, text: str, is_violation: bool, capture_source: str = "http") -> dict:
        parsed = parse_tiktok_link(text)
        if not parsed:
            return {"ok": False, "error": "未识别到 TikTok 链接"}
        seq = self._enqueue_parsed_link(parsed, is_violation=is_violation, capture_source=capture_source)
        return {"ok": True, "seq": seq, "is_violation": is_violation}

    def _enqueue_parsed_link(self, parsed: dict, is_violation: bool, capture_source: str) -> int:
        now = time.time()
        duplicate_seq = None
        with self._state_lock:
            for url, recent in list(self._recent_urls.items()):
                if now - recent["time"] > RECENT_DUPLICATE_WINDOW_SECONDS:
                    self._recent_urls.pop(url, None)

            recent = self._recent_urls.get(parsed["normalized_url"])
            if recent and now - recent["time"] <= RECENT_DUPLICATE_WINDOW_SECONDS:
                duplicate_seq = recent["seq"]
                item = self._items.get(duplicate_seq)
                if item and is_violation and not item.get("violation_requested"):
                    self._pending_violation = item

        if duplicate_seq is not None:
            if is_violation:
                print(f"\n#{duplicate_seq} ✅ 收到同一链接的违规直传，正在升级为“违规”")
                self._mark_pending_violation()
            else:
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
                "status": "queued",
            }
            self._items[seq] = item
            self._pending_violation = item
            self._recent_urls[parsed["normalized_url"]] = {"seq": seq, "time": now}
        print(f"\n#{seq} [{datetime.now().strftime('%H:%M:%S')}] 检测到 TikTok 链接:")
        print(f"#{seq} 视频ID: {parsed['video_id'] or '未知'}")
        print(f"#{seq} 链接: {parsed['normalized_url']}")
        print(f"#{seq} 判定: {'违规' if is_violation else '不违规'}")
        print(f"#{seq} 来源: {capture_source}")
        append_capture_log({
            "seq": seq,
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "url": parsed["normalized_url"],
            "video_id": parsed["video_id"],
            "is_violation": is_violation,
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
            try:
                result = None
                for attempt in range(1, WRITE_MAX_RETRIES + 1):
                    result = self.manager.record_feed_video(
                        video_url=item["url"],
                        video_id=item.get("video_id"),
                        is_violation=write_as_violation,
                        task_name=self.task_name,
                        source_label=self.source_label,
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
                    time.sleep(min(2 ** attempt, 10))
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
                        }
                        if self._pending_violation and self._pending_violation.get("seq") == seq:
                            self._pending_violation["record_id"] = record_id
                        update_after_write = bool(item.get("violation_requested")) and not write_as_violation
                    if update_after_write:
                        mark_result = self.manager.mark_record_violation(record_id)
                        if mark_result.get("action") == "updated":
                            print(f"#{seq} ✅ 已写入：违规")
                        else:
                            print(f"\n#{seq} 修正失败: {mark_result.get('error')}")
                    else:
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

    def _mark_pending_violation(self):
        with self._state_lock:
            pending = self._pending_violation
            if not pending or time.time() > pending["deadline"]:
                self._pending_violation = None
                print("\n⚠️ 没有可标记的最近链接：请先复制 TikTok 链接，再长按悬浮球。")
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
        while len(self._worker_threads) < WRITE_WORKER_COUNT:
            worker = threading.Thread(target=self._record_worker_loop, daemon=True)
            worker.start()
            self._worker_threads.append(worker)
        self._start_http_server()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

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
            return
        monitor = self

        class RecordHandler(BaseHTTPRequestHandler):
            def _send_json(self, status: int, payload: dict):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed_url = urlparse(self.path)
                if parsed_url.path not in ("/record", "/health"):
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                if parsed_url.path == "/health":
                    self._send_json(200, {"ok": True})
                    return
                params = parse_qs(parsed_url.query)
                link = (params.get("url") or params.get("link") or [""])[0]
                violation_raw = (params.get("violation") or params.get("is_violation") or ["0"])[0]
                is_violation = str(violation_raw).lower() in ("1", "true", "yes", "y", "是", "违规")
                result = monitor.submit_external_link(link, is_violation, "iphone-http")
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
                result = monitor.submit_external_link(link, is_violation, "iphone-http")
                self._send_json(200 if result.get("ok") else 400, result)

            def log_message(self, format, *args):
                return

        try:
            self._http_server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), RecordHandler)
        except OSError as e:
            print(f"⚠️ HTTP 直传服务启动失败：{e}")
            print("仍会继续使用剪贴板监听备用模式。")
            return
        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        lan_ip = get_lan_ip()
        print(f"HTTP 直传已开启：http://{lan_ip}:{HTTP_PORT}/record")
        print("iPhone 快捷指令可直接请求该地址，绕过 Apple 跨设备剪贴板。")


def run_monitor(monitor: ClipboardMonitor):
    monitor.start()
    print("【推荐】极速模式：iPhone 复制链接后，用快捷指令提交到 Mac。")
    print("【备用】只复制链接仍会走 Apple 跨设备剪贴板监听，但可能慢或漏。按 q 退出。")
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
        app_token = config.get_env("BITABLE_APP_TOKEN")
        table_id = config.get_env("BITABLE_TABLE_ID")
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

    from feishu_bitable import FeishuBitableClient
    from record_manager import RecordManager

    try:
        bitable_client = FeishuBitableClient()
    except Exception as e:
        print(f"初始化飞书多维表格客户端失败: {e}")
        return

    manager = RecordManager(bitable_client)
    
    # 使用固定或继承的名称，不再繁琐询问
    task_name = "自动记录任务"
    source_label = target_name
    
    monitor = ClipboardMonitor(manager, task_name, source_label, target_name)
    run_monitor(monitor)


if __name__ == "__main__":
    main()
