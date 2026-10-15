import time
from typing import Optional, List, Dict, Any, Tuple
import requests
import config

# 飞书多维表格字段类型：15 = 超链接（URL）
FIELD_TYPE_URL = 15
# 字段类型缓存有效期（秒）
FIELD_CACHE_TTL = 600


def fields_to_retry(fields: Optional[Dict[str, Any]] = None) -> List[str]:
    optional = [
        config.FIELD_TASK,
        config.FIELD_SOURCE_LABEL,
        config.FIELD_BEHAVIOR_TAGS,
        config.FIELD_RECORDED_AT,
        config.FIELD_WRITE_STATUS,
        config.FIELD_DEDUPE_KEY,
    ]
    if fields is None:
        return optional
    return [name for name in optional if name in fields]


class FeishuBitableClient:
    def __init__(self):
        self.app_id = config.get_env("FEISHU_APP_ID")
        self.app_secret = config.get_env("FEISHU_APP_SECRET")
        self.app_token = config.get_env("BITABLE_APP_TOKEN")
        self.table_id = config.get_env("BITABLE_TABLE_ID")
        self._tenant_access_token = None
        self._token_expire_time = 0
        self._field_type_cache: Optional[Dict[str, int]] = None
        self._field_type_cache_time = 0.0

    def _get_tenant_access_token(self) -> str:
        if self._tenant_access_token and time.time() < self._token_expire_time - 60:
            return self._tenant_access_token

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取 tenant_access_token 失败: {data}")
        self._tenant_access_token = data["tenant_access_token"]
        self._token_expire_time = time.time() + data.get("expire", 7200)
        return self._tenant_access_token

    def _headers(self) -> dict:
        token = self._get_tenant_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _base_url(self) -> str:
        return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}"

    def get_field_type_map(self, force: bool = False) -> Dict[str, int]:
        """拉取并缓存 {字段名: 字段类型}，用于按列类型转换写入值。"""
        now = time.time()
        if (
            not force
            and self._field_type_cache is not None
            and now - self._field_type_cache_time < FIELD_CACHE_TTL
        ):
            return self._field_type_cache

        url = f"{self._base_url()}/fields"
        type_map: Dict[str, int] = {}
        page_token: Optional[str] = None
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=self._headers(), params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"获取字段列表失败: {data}")
            payload = data.get("data", {})
            for item in payload.get("items", []):
                name = item.get("field_name")
                ftype = item.get("type")
                if name is not None and ftype is not None:
                    type_map[name] = ftype
            if not payload.get("has_more"):
                break
            page_token = payload.get("page_token")
            if not page_token:
                break

        self._field_type_cache = type_map
        self._field_type_cache_time = now
        return type_map

    @staticmethod
    def _build_url_value(val: Any) -> Optional[Dict[str, str]]:
        """把纯文本链接转成飞书超链接字段需要的对象；空值返回 None（应丢弃该字段）。"""
        if val is None:
            return None
        if isinstance(val, dict):
            link = str(val.get("link", "")).strip()
            if not link:
                return None
            return {"link": link, "text": str(val.get("text") or link)}
        if isinstance(val, list):
            # 兼容富文本数组：取第一个可用链接
            for part in val:
                if isinstance(part, dict):
                    link = str(part.get("link", "") or part.get("text", "")).strip()
                    if link.startswith(("http://", "https://")):
                        return {"link": link, "text": link}
            return None
        text = str(val).strip()
        if not text:
            return None
        if not text.lower().startswith(("http://", "https://")):
            # 非链接内容不能写进超链接列，交给上层决定（一般不会出现）
            return {"link": text, "text": text} if text.startswith("//") else None
        return {"link": text, "text": text}

    @staticmethod
    def _to_plain_text(val: Any) -> str:
        """把链接值（可能是超链接对象/数组/字符串）压平成纯文本，用于文本列写入。"""
        if val is None:
            return ""
        if isinstance(val, dict):
            return str(val.get("link") or val.get("text") or "").strip()
        if isinstance(val, list):
            for part in val:
                if isinstance(part, dict):
                    text = str(part.get("link") or part.get("text") or "").strip()
                    if text:
                        return text
                elif part:
                    return str(part).strip()
            return ""
        return str(val).strip()

    def _resolve_link_mode(self, type_map: Optional[Dict[str, int]]) -> str:
        """决定「链接」列按文本还是超链接写入。"""
        mode = (config.LINK_WRITE_MODE or "auto").lower()
        if mode in ("text", "url"):
            return mode
        # auto：按飞书实际列类型自动选择
        if (type_map or {}).get(config.FIELD_URL) == FIELD_TYPE_URL:
            return "url"
        return "text"

    def _normalize_fields(
        self, fields: Dict[str, Any], type_map: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """按列类型/配置转换写入值。「链接」列可写文本或超链接；空链接字段直接丢弃。"""
        if not fields:
            return {}
        if type_map is None:
            try:
                type_map = self.get_field_type_map()
            except Exception:
                type_map = {}
        link_mode = self._resolve_link_mode(type_map)
        out: Dict[str, Any] = {}
        for name, val in fields.items():
            if name == config.FIELD_URL:
                if link_mode == "url":
                    built = self._build_url_value(val)
                    if built is None:
                        continue
                    out[name] = built
                else:
                    text = self._to_plain_text(val)
                    if not text:
                        continue
                    out[name] = text
            elif type_map.get(name) == FIELD_TYPE_URL:
                built = self._build_url_value(val)
                if built is None:
                    continue
                out[name] = built
            else:
                out[name] = val
        return out

    @staticmethod
    def _coerce_url_like(fields: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """兜底：拿不到字段元数据时，把所有 http(s) 字符串转成超链接对象。"""
        out: Dict[str, Any] = {}
        changed = False
        for name, val in fields.items():
            if isinstance(val, str) and val.strip().lower().startswith(("http://", "https://")):
                link = val.strip()
                out[name] = {"link": link, "text": link}
                changed = True
            elif name == config.FIELD_URL and (val is None or str(val).strip() == ""):
                # 空链接列直接丢弃，避免 URLFieldConvFail
                changed = True
                continue
            else:
                out[name] = val
        return out, changed

    def _post_create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url()}/records"
        resp = requests.post(
            url, headers=self._headers(), json={"fields": fields}, timeout=10
        )
        # 飞书可能用 HTTP 200 或 4xx 状态返回同样的业务错误体。
        # 必须先解析 JSON，否则 raise_for_status 会丢掉错误码与字段名，
        # 导致 1254068 兜底和“按字段名自动降级”失效。
        try:
            return resp.json()
        except ValueError:
            resp.raise_for_status()
            raise Exception(f"创建记录失败: HTTP {resp.status_code}，响应无法解析")


    def list_records(self, page_size: int = 100, page_token: Optional[str] = None) -> dict:
        url = f"{self._base_url()}/records"
        params = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=self._headers(), params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"获取记录列表失败: {data}")
        return data.get("data", {})

    def list_all_records(self) -> List[Dict[str, Any]]:
        all_records = []
        page_token = None
        while True:
            result = self.list_records(page_size=500, page_token=page_token)
            items = result.get("items", [])
            all_records.extend(items)
            if not result.get("has_more"):
                break
            page_token = result.get("page_token")
            if not page_token:
                break
        return all_records

    def search_records(self, filter_formula: str) -> List[Dict[str, Any]]:
        url = f"{self._base_url()}/records/search"
        payload = {
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": config.FIELD_URL,
                        "operator": "contains",
                        "value": [filter_formula],
                    }
                ],
            }
        }
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        return data.get("data", {}).get("items", [])

    def _request_create_record(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        # 先按真实列类型转换（超链接列 -> 对象），兼容文本/超链接两种表格
        try:
            type_map = self.get_field_type_map()
        except Exception:
            type_map = {}
        norm_fields = self._normalize_fields(fields, type_map)

        data = self._post_create(norm_fields)
        if data.get("code") != 0:
            # 兜底：字段元数据没拿到或判断遗漏时，遇到超链接格式错误，自动转换后重发一次
            if data.get("code") == 1254068:
                coerced, changed = self._coerce_url_like(norm_fields)
                if changed:
                    data = self._post_create(coerced)
            if data.get("code") != 0:
                raise Exception(f"创建记录失败: {data}")
        return data.get("data", {}).get("record", {})

    def create_record(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        retry_fields = dict(fields)
        optional_fields = fields_to_retry(retry_fields)
        while True:
            try:
                return self._request_create_record(retry_fields)
            except Exception as e:
                text = str(e)
                removed = False
                for field_name in list(optional_fields):
                    if field_name in retry_fields and field_name in text:
                        retry_fields.pop(field_name, None)
                        optional_fields.remove(field_name)
                        removed = True
                        # 屏蔽这类报错，只在底层抛出，不打印到终端
                        break
                if not removed:
                    raise

    def update_record(self, record_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url()}/records/{record_id}"
        try:
            type_map = self.get_field_type_map()
        except Exception:
            type_map = {}
        norm_fields = self._normalize_fields(fields, type_map)
        resp = requests.put(url, headers=self._headers(), json={"fields": norm_fields}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"更新记录失败: {data}")
        return data.get("data", {}).get("record", {})

    def find_record_by_url(self, video_url: str, video_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        records = self.list_all_records()
        for record in records:
            fields = record.get("fields", {})
            url_val = fields.get(config.FIELD_URL, "")
            if isinstance(url_val, list):
                url_text = " ".join([str(u.get("link", "") or u.get("text", "")) for u in url_val])
            else:
                url_text = str(url_val)
            if video_id and video_id in url_text:
                return record
            if url_text == video_url:
                return record
        return None

    def get_field_value(self, record: Dict[str, Any], field_name: str) -> str:
        fields = record.get("fields", {})
        val = fields.get(field_name, "")
        if isinstance(val, list):
            if len(val) > 0:
                first = val[0]
                if isinstance(first, dict):
                    return str(first.get("text", first.get("value", "")))
                return str(first)
            return ""
        if isinstance(val, dict):
            return str(val.get("text", val.get("value", "")))
        return str(val) if val else ""
