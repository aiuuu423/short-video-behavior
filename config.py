import os
import glob
from dotenv import load_dotenv

ENV_PREFIX = ".env."
DEFAULT_ENV_FILE = ".env"
CONFIG_DIR = "configs"
DATA_DIR = "data"
LOG_DIR = "logs"
TASKS_FILE = os.path.join(CONFIG_DIR, "tasks.json")
SOURCE_LABELS_FILE = os.path.join(CONFIG_DIR, "source_labels.json")
PROCESSED_LINKS_FILE = os.path.join(DATA_DIR, "processed_links.json")
FAILED_RECORDS_FILE = os.path.join(DATA_DIR, "failed_records.json")
RUNTIME_STATE_FILE = os.path.join(DATA_DIR, "runtime_state.json")


def list_feishu_targets() -> list:
    pattern = f"{ENV_PREFIX}*"
    files = glob.glob(pattern)
    targets = []
    for f in sorted(files):
        name = f[len(ENV_PREFIX):]
        if name == "example":
            continue
        targets.append({"name": name, "file": f})
    return targets


def list_accounts() -> list:
    return list_feishu_targets()


def load_account(account_name: str):
    env_file = f"{ENV_PREFIX}{account_name}"
    if not os.path.exists(env_file):
        raise FileNotFoundError(f"飞书写入配置不存在: {env_file}")
    load_dotenv(env_file, override=True)
    return env_file


def create_account(account_name: str, app_id: str, app_secret: str,
                   bitable_app_token: str, bitable_table_id: str):
    env_file = f"{ENV_PREFIX}{account_name}"
    content = f"""FEISHU_APP_ID={app_id}
FEISHU_APP_SECRET={app_secret}

BITABLE_APP_TOKEN={bitable_app_token}
BITABLE_TABLE_ID={bitable_table_id}

POLL_INTERVAL=0.01
IDLE_THRESHOLD_MINUTES=10
"""
    with open(env_file, "w", encoding="utf-8") as f:
        f.write(content)
    return env_file


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _init():
    if os.path.exists(DEFAULT_ENV_FILE):
        load_dotenv(DEFAULT_ENV_FILE, override=False)


_init()

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

BITABLE_APP_TOKEN = os.getenv("BITABLE_APP_TOKEN", "")
BITABLE_TABLE_ID = os.getenv("BITABLE_TABLE_ID", "")

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1.0"))
IDLE_THRESHOLD_MINUTES = int(os.getenv("IDLE_THRESHOLD_MINUTES", "10"))

TIKTOK_DOMAINS = [
    "tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "m.tiktok.com",
    "www.tiktok.com",
]

LOCAL_CACHE_FILE = "cache.json"

FIELD_START_TIME = "起始时间"
FIELD_BEHAVIOR = "行为"
FIELD_KEYWORD = "搜索词"
FIELD_URL = "链接"
FIELD_QUALIFIED = os.getenv("FIELD_QUALIFIED", "是否违规")
FIELD_FYF_DENSITY = "FYF密度"
FIELD_TASK = os.getenv("FIELD_TASK", "采集任务")
FIELD_SOURCE_LABEL = os.getenv("FIELD_SOURCE_LABEL", "TikTok 来源标签")
FIELD_BEHAVIOR_TAGS = os.getenv("FIELD_BEHAVIOR_TAGS", "行为标签")
FIELD_RECORDED_AT = os.getenv("FIELD_RECORDED_AT", "记录时间")
FIELD_WRITE_STATUS = os.getenv("FIELD_WRITE_STATUS", "写入状态")
FIELD_DEDUPE_KEY = os.getenv("FIELD_DEDUPE_KEY", "去重 Key")
