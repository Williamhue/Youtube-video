import re
from typing import Optional
from googleapiclient.discovery import build
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY = os.getenv("YOUTUBE_API_KEY")

_YT_PATTERNS = [
    r"(?:v=)([A-Za-z0-9_-]{11})",            # watch?v=ID
    r"youtu\.be/([A-Za-z0-9_-]{11})",        # youtu.be/ID
    r"shorts/([A-Za-z0-9_-]{11})",           # shorts/ID
    r"embed/([A-Za-z0-9_-]{11})",            # embed/ID
]

def extract_video_id(url_or_id: str) -> Optional[str]:
    s = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    for p in _YT_PATTERNS:
        m = re.search(p, s)
        if m:
            return m.group(1)
    return None

def get_youtube_client():
    if not API_KEY:
        raise RuntimeError("Missing YOUTUBE_API_KEY in .env")
    return build("youtube", "v3", developerKey=API_KEY)
