import re
from typing import Optional
from urllib.parse import urlparse, urlunparse
from config import TIKTOK_DOMAINS

VIDEO_ID_PATTERNS = [
    re.compile(r"/video/(\d+)"),
    re.compile(r"/v/(\d+)"),
]

SHORT_LINK_PATTERN = re.compile(r"https?://(vm|vt)\.tiktok\.com/([A-Za-z0-9]+)/?")


def is_tiktok_url(text: str) -> bool:
    if not text or not text.strip():
        return False
    text = text.strip()
    for domain in TIKTOK_DOMAINS:
        if domain in text.lower():
            return True
    return False


def extract_urls(text: str) -> list:
    url_pattern = re.compile(r"https?://[^\s]+", re.IGNORECASE)
    urls = url_pattern.findall(text)
    return [u for u in urls if is_tiktok_url(u)]


def extract_video_id(url: str) -> Optional[str]:
    for pattern in VIDEO_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def normalize_url(url: str, video_id: Optional[str] = None) -> str:
    if video_id:
        return f"https://www.tiktok.com/@user/video/{video_id}"
    try:
        parsed = urlparse(url)
        clean = parsed._replace(query="", fragment="")
        return urlunparse(clean)
    except Exception:
        return url


def parse_tiktok_link(text: str) -> Optional[dict]:
    # 快速前置检查，避免在大量非 URL 文本上做正则计算
    if "tiktok.com" not in text:
        return None

    urls = extract_urls(text)
    if not urls:
        return None
    url = urls[0]
    video_id = extract_video_id(url)
    normalized = normalize_url(url, video_id)
    return {
        "raw_url": url,
        "normalized_url": normalized,
        "video_id": video_id,
    }
