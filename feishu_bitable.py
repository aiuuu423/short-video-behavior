import time
from typing import Optional, List, Dict, Any
import requests
import config


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
        url = f"{self._base_url()}/records"
        payload = {"fields": fields}
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
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
        payload = {"fields": fields}
        resp = requests.put(url, headers=self._headers(), json=payload, timeout=10)
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
