import csv
import io
import json
import re
import subprocess
import threading
from datetime import datetime
from typing import Any, Dict, List, Tuple

import config


class FeishuSheetClient:
    """通过 lark-cli 以用户身份写入飞书 Sheet，接口对齐 FeishuBitableClient。"""

    def __init__(self):
        self.url = config.get_env("SHEET_URL")
        self.sheet_id = config.get_env("SHEET_ID")
        self.sheet_name = config.get_env("SHEET_NAME")
        self.header_range = config.get_env("SHEET_HEADER_RANGE", "A1:Z1")
        self.as_identity = config.get_env("LARK_CLI_AS", "user") or "user"
        self._headers_cache = None
        self._write_lock = threading.Lock()
        if not self.url:
            raise ValueError("SHEET_URL 不能为空")
        if not self.sheet_id and not self.sheet_name:
            raise ValueError("SHEET_ID 或 SHEET_NAME 至少配置一个")

    def _sheet_args(self) -> List[str]:
        args = ["--url", self.url]
        if self.sheet_id:
            args.extend(["--sheet-id", self.sheet_id])
        else:
            args.extend(["--sheet-name", self.sheet_name])
        return args

    def _run(self, args: List[str], timeout: int = 30) -> Dict[str, Any]:
        cmd = ["lark-cli"] + args + ["--as", self.as_identity]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise Exception(f"lark-cli 执行失败: {err}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise Exception(f"lark-cli 返回非 JSON: {proc.stdout[:500]}") from exc
        if not data.get("ok"):
            raise Exception(f"lark-cli 返回失败: {data}")
        return data.get("data", {})

    @staticmethod
    def _parse_last_row(current_region: str) -> int:
        match = re.search(r":\$?[A-Z]+(\d+)$|:[A-Z]+(\d+)$", current_region or "")
        if not match:
            return 1
        return int(next(g for g in match.groups() if g))

    @staticmethod
    def _strip_row_prefix(line: str) -> str:
        return re.sub(r"^\[row=\d+\]\s*", "", line)

    def _read_header_info(self, force: bool = False) -> Tuple[List[str], Dict[str, str], int, str]:
        if self._headers_cache and not force:
            return self._headers_cache
        data = self._run(["sheets", "+csv-get", *self._sheet_args(), "--range", self.header_range])
        annotated = data.get("annotated_csv", "")
        first_line = annotated.splitlines()[0] if annotated else ""
        row_text = self._strip_row_prefix(first_line)
        headers = next(csv.reader([row_text])) if row_text else []
        col_indices = data.get("col_indices", [])
        col_map = {}
        for idx, name in enumerate(headers):
            if name and idx < len(col_indices):
                col_map[name] = col_indices[idx]
        last_row = self._parse_last_row(data.get("current_region", ""))
        sheet_name = self.sheet_name or data.get("sheet_name", "")
        self._headers_cache = (headers, col_map, last_row, sheet_name)
        return self._headers_cache

    @staticmethod
    def _format_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, int) and value > 10_000_000_000:
            return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, list):
            return ",".join(str(v) for v in value if v)
        if isinstance(value, dict):
            return str(value.get("link") or value.get("text") or "")
        return str(value)

    @staticmethod
    def _csv_text(rows: List[List[Any]]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for row in rows:
            writer.writerow([FeishuSheetClient._format_value(v) for v in row])
        return buf.getvalue().rstrip("\n")

    def _sheet_payload(self, columns: List[str], data: List[List[Any]], mode: str = "append") -> str:
        sheet_name = self.sheet_name or self._read_header_info()[3]
        dtypes = {name: "object" for name in columns}
        return json.dumps(
            {
                "sheets": [
                    {
                        "name": sheet_name,
                        "mode": mode,
                        "columns": columns,
                        "dtypes": dtypes,
                        "data": data,
                    }
                ]
            },
            ensure_ascii=False,
        )

    def _normalize_fields_for_headers(self, fields: Dict[str, Any], headers: List[str]) -> Dict[str, Any]:
        out = dict(fields)
        if config.FIELD_KEYWORD not in headers and "搜索词汇" in headers and "搜索词" in out:
            out["搜索词汇"] = out.pop("搜索词")
        return out

    def create_record(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        with self._write_lock:
            headers, _, last_row, _ = self._read_header_info(force=True)
            fields = self._normalize_fields_for_headers(fields, headers)
            columns = [name for name in headers if name and name in fields]
            if not columns:
                raise Exception("没有可写入的 Sheet 表头字段")
            row = [fields.get(name, "") for name in columns]
            self._run(
                [
                    "sheets",
                    "+table-put",
                    "--url",
                    self.url,
                    "--sheets",
                    self._sheet_payload(columns, [row], mode="append"),
                ],
                timeout=45,
            )
            # table-put 追加成功后，当前记录位于旧 current_region 的下一行。
            self._headers_cache = None
            return {"record_id": f"row:{last_row + 1}"}

    def update_record(self, record_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if not record_id.startswith("row:"):
            raise Exception(f"Sheet 记录 ID 无效: {record_id}")
        row_no = int(record_id.split(":", 1)[1])
        headers, col_map, _, _ = self._read_header_info()
        fields = self._normalize_fields_for_headers(fields, headers)
        for name, value in fields.items():
            col = col_map.get(name)
            if not col:
                continue
            self._run(
                [
                    "sheets",
                    "+csv-put",
                    *self._sheet_args(),
                    "--start-cell",
                    f"{col}{row_no}",
                    "--csv",
                    self._csv_text([[value]]),
                ],
                timeout=30,
            )
        return {"record_id": record_id, "fields": fields}

    def list_all_records(self) -> List[Dict[str, Any]]:
        headers, _, last_row, _ = self._read_header_info(force=True)
        last_col = "Z"
        for idx in range(len(headers) - 1, -1, -1):
            if headers[idx]:
                last_col = chr(ord("A") + idx)
                break
        data = self._run(["sheets", "+csv-get", *self._sheet_args(), "--range", f"A1:{last_col}{last_row}"])
        rows = []
        for line in (data.get("annotated_csv") or "").splitlines()[1:]:
            row_text = self._strip_row_prefix(line)
            values = next(csv.reader([row_text])) if row_text else []
            row_fields = {}
            for idx, name in enumerate(headers):
                if name and idx < len(values) and values[idx] != "":
                    row_fields[name] = values[idx]
            if row_fields:
                rows.append({"fields": row_fields})
        return rows
