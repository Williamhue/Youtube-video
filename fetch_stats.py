# fetch_stats.py — requests 直连 YouTube Data API v3（稳、可控重试/代理）
import os
import sys
import re
import time
from datetime import datetime
from typing import Optional, List, Dict

import pandas as pd
import requests
from dotenv import load_dotenv
from pytz import timezone

# ---------------- 基础设置 ----------------
load_dotenv()
API_KEY = os.getenv("YOUTUBE_API_KEY")
if not API_KEY:
    print("❌ 缺少环境变量 YOUTUBE_API_KEY（请在 .env 中配置）")
    sys.exit(1)

# 可选代理（如需走代理，.env 里设置 HTTP_PROXY / HTTPS_PROXY）
PROXIES = {
    "http": os.getenv("HTTP_PROXY"),
    "https": os.getenv("HTTPS_PROXY"),
}
USE_PROXIES = any(PROXIES.values())

INPUT_CSV = "inputs/videos.csv"
DATA_CSV = "data/history.csv"
LOG_DIR = "logs"

os.makedirs("data", exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

LA_TZ = timezone("America/Los_Angeles")
BASE_URL = "https://www.googleapis.com/youtube/v3/videos"

# ---------------- 工具函数 ----------------
_YT_PATTERNS = [
    r"(?:v=)([A-Za-z0-9_-]{11})",      # watch?v=ID
    r"youtu\.be/([A-Za-z0-9_-]{11})",  # youtu.be/ID
    r"shorts/([A-Za-z0-9_-]{11})",     # shorts/ID
    r"embed/([A-Za-z0-9_-]{11})",      # embed/ID
]

def extract_video_id(url_or_id: str) -> Optional[str]:
    s = (url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    for p in _YT_PATTERNS:
        m = re.search(p, s)
        if m:
            return m.group(1)
    return None

def today_la_str() -> str:
    return datetime.now(LA_TZ).date().isoformat()

def chunk(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def read_video_ids() -> List[str]:
    if not os.path.exists(INPUT_CSV):
        print(f"❌ 未找到 {INPUT_CSV}")
        sys.exit(1)

    df = pd.read_csv(INPUT_CSV)
    # 兼容：若没有表头“video”，把第一列当 video
    if "video" not in df.columns:
        if len(df.columns) >= 1:
            df = df.rename(columns={df.columns[0]: "video"})
        else:
            raise ValueError("inputs/videos.csv 没有可用列。")

    ids = []
    for raw in df["video"].dropna().astype(str):
        vid = extract_video_id(raw)
        if vid:
            ids.append(vid)

    # 去重，保持顺序
    ids = list(dict.fromkeys(ids))
    if not ids:
        print("⚠️ inputs/videos.csv 中未解析到任何视频 ID")
    return ids

# ---------------- 请求封装（含重试） ----------------
def get_video_items(video_ids: List[str]) -> List[Dict]:
    """
    用 requests 获取一批视频的 snippet+statistics，带超时与退避重试。
    """
    params = {
        "part": "snippet,statistics",
        "id": ",".join(video_ids),
        "key": API_KEY,
    }

    backoff = [0, 2, 5]  # 3 次：立刻、2s、5s
    last_err = None

    for delay in backoff:
        try:
            if delay:
                time.sleep(delay)
            resp = requests.get(
                BASE_URL,
                params=params,
                timeout=20,
                proxies=PROXIES if USE_PROXIES else None,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", [])
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            last_err = e
            continue
        except requests.exceptions.HTTPError as e:
            # 4xx/5xx：打印服务端信息后直接抛出，方便定位（例如配额/Key限制）
            text = getattr(e.response, "text", "")[:300]
            print(f"HTTPError: {e} — {text}")
            raise
        except Exception as e:
            last_err = e
            continue

    if last_err:
        raise last_err
    return []

# ---------------- 主流程 ----------------
def main():
    video_ids = read_video_ids()
    if not video_ids:
        return

    all_rows = []
    # 小批量更稳（25 一批）
    for batch in chunk(video_ids, 25):
        items = get_video_items(batch)
        for it in items:
            vid = it.get("id")
            snip = it.get("snippet", {}) or {}
            stats = it.get("statistics", {}) or {}

            thumbs = snip.get("thumbnails", {}) or {}
            thumb = (
                (thumbs.get("maxres") or {}).get("url") or
                (thumbs.get("high")   or {}).get("url") or
                (thumbs.get("medium") or {}).get("url") or
                (thumbs.get("default") or {}).get("url")
            )

            def _to_int(x):
                try:
                    return int(x)
                except Exception:
                    return 0

            row = {
                "date": today_la_str(),
                "video_id": vid,
                "views": _to_int(stats.get("viewCount")),
                "likes": _to_int(stats.get("likeCount")),
                "comments": _to_int(stats.get("commentCount")),
                "title": snip.get("title", ""),
                "channel_title": snip.get("channelTitle", ""),
                "published_at": snip.get("publishedAt", ""),
                "thumbnail_url": thumb,
                "video_url": f"https://www.youtube.com/watch?v={vid}",
            }
            all_rows.append(row)

    new_df = pd.DataFrame(all_rows)

    if os.path.exists(DATA_CSV) and os.path.getsize(DATA_CSV) > 0:
        old_df = pd.read_csv(DATA_CSV)
        merged = pd.concat([old_df, new_df], ignore_index=True)
        merged = (
            merged.sort_values(["video_id", "date"])
                  .drop_duplicates(subset=["video_id", "date"], keep="last")
        )
    else:
        merged = new_df

    merged.to_csv(DATA_CSV, index=False)
    print(f"✅ Saved {len(new_df)} rows. History size: {len(merged)}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 抓取失败：{e}")
        sys.exit(1)