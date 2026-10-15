import re
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qs
from config import PLATFORM_DOMAINS

# 各平台视频 ID 提取规则（按优先级匹配，第一个命中即用）
VIDEO_ID_PATTERNS = {
    "tiktok": [
        re.compile(r"/video/(\d+)"),
        re.compile(r"/v/(\d+)"),
        re.compile(r"https?://(?:vm|vt)\.tiktok\.com/([A-Za-z0-9]+)"),
    ],
    "youtube": [
        re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})"),
        re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})"),
        re.compile(r"/shorts/([A-Za-z0-9_-]{6,})"),
        re.compile(r"/embed/([A-Za-z0-9_-]{6,})"),
    ],
    "instagram": [
        re.compile(r"/reels?/([A-Za-z0-9_-]+)"),
        re.compile(r"/p/([A-Za-z0-9_-]+)"),
        re.compile(r"/tv/([A-Za-z0-9_-]+)"),
    ],
    "facebook": [
        re.compile(r"/videos/(\d+)"),
        re.compile(r"[?&]v=(\d+)"),
        re.compile(r"/reel/(\d+)"),
        re.compile(r"fb\.watch/([A-Za-z0-9_-]+)"),
    ],
}


def detect_platform(url: str) -> Optional[str]:
    """根据域名判断链接属于哪个平台。"""
    if not url or not url.strip():
        return None
    low = url.strip().lower()
    for platform, domains in PLATFORM_DOMAINS.items():
        for domain in domains:
            if domain in low:
                return platform
    return None


def is_video_url(text: str) -> bool:
    return detect_platform(text) is not None


def extract_urls(text: str) -> list:
    url_pattern = re.compile(r"https?://[^\s]+", re.IGNORECASE)
    urls = url_pattern.findall(text)
    return [u for u in urls if is_video_url(u)]


def extract_video_id(url: str, platform: Optional[str] = None) -> Optional[str]:
    platform = platform or detect_platform(url)
    if not platform:
        return None
    for pattern in VIDEO_ID_PATTERNS.get(platform, []):
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def normalize_url(url: str, platform: Optional[str] = None, video_id: Optional[str] = None) -> str:
    """生成稳定的规范化链接，用于去重。保留可定位视频的核心信息，去掉跟踪参数。"""
    platform = platform or detect_platform(url)
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    # YouTube：标准视频用 watch?v=，否则保留 youtu.be/shorts 形式
    if platform == "youtube":
        qs = parse_qs(parsed.query)
        vid = (qs.get("v") or [None])[0] or video_id
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"

    # TikTok：数字 video_id 可还原为标准长链；短链保持原样（去掉查询参数）
    if platform == "tiktok" and video_id and video_id.isdigit():
        return f"https://www.tiktok.com/@user/video/{video_id}"

    # Facebook：watch?v= 形式的视频 ID 在 query 里，清掉 query 会丢失定位信息，需保留
    if platform == "facebook" and video_id and "v=" in parsed.query:
        return f"https://www.facebook.com/watch/?v={video_id}"

    # 其余平台：去掉 query/fragment 保留干净路径
    clean = parsed._replace(query="", fragment="")
    normalized = urlunparse(clean)
    return normalized.rstrip("/") or url


def parse_video_link(text: str) -> Optional[dict]:
    """从任意文本中解析出受支持平台（TikTok/YouTube/Instagram/Facebook）的视频链接。"""
    if not text:
        return None
    # 快速前置检查，避免在大量非 URL 文本上做正则计算
    low = text.lower()
    all_domains = [d for domains in PLATFORM_DOMAINS.values() for d in domains]
    if not any(d in low for d in all_domains):
        return None

    urls = extract_urls(text)
    if not urls:
        return None
    url = urls[0]
    platform = detect_platform(url)
    video_id = extract_video_id(url, platform)
    normalized = normalize_url(url, platform, video_id)
    return {
        "raw_url": url,
        "normalized_url": normalized,
        "video_id": video_id,
        "platform": platform,
    }


# 向后兼容别名：旧代码可能仍以 parse_tiktok_link 调用
def parse_tiktok_link(text: str) -> Optional[dict]:
    return parse_video_link(text)
