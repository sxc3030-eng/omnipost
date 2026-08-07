"""
OmniPost — All-in-one Social Media Marketing Tool
Backend Python principal
Version: 1.0.0
Usage: python omnipost.py
"""

import asyncio
import json
import logging
import os
import sys
import time
import threading
import secrets
import hashlib
import hmac
import base64
import mimetypes
import pathlib
import urllib.request
import urllib.parse
import urllib.error
import subprocess
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False
    print("[WARN] pip install websockets")

try:
    from genia_listener import listener_loop as genia_listener_loop
    HAS_GENIA = True
except ImportError:
    HAS_GENIA = False
    print("[WARN] genia_listener.py not found — simple GeniA cross-posting disabled")

try:
    import genia_pipeline
    HAS_PIPELINE = True
except ImportError:
    HAS_PIPELINE = False
    print("[WARN] genia_pipeline.py not found — GeniA pipeline disabled")

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("omnipost.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("omnipost")

# ── Config ─────────────────────────────────────────────────────────────────
SETTINGS_FILE  = "omnipost_settings.json"
POSTS_FILE     = "omnipost_posts.json"
ANALYTICS_FILE = "omnipost_analytics.json"
MEDIA_DIR      = "media"
REPORTS_DIR    = "reports"
WS_PORT        = 8860
AUTH_PORT      = 8861

os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# ── Platforms ──────────────────────────────────────────────────────────────
PLATFORMS = {
    "facebook":  {"name": "Facebook",  "icon": "📘", "color": "#1877f2", "max_chars": 63206},
    "instagram": {"name": "Instagram", "icon": "📸", "color": "#e1306c", "max_chars": 2200},
    "tiktok":    {"name": "TikTok",    "icon": "🎵", "color": "#ff0050", "max_chars": 2200},
    "youtube":   {"name": "YouTube",   "icon": "▶️", "color": "#ff0000", "max_chars": 5000},
    "pinterest": {"name": "Pinterest", "icon": "📌", "color": "#e60023", "max_chars": 500},
    "twitter":   {"name": "Twitter/X", "icon": "🐦", "color": "#1da1f2", "max_chars": 280},
    "linkedin":  {"name": "LinkedIn",  "icon": "💼", "color": "#0077b5", "max_chars": 3000},
}

# ── State ──────────────────────────────────────────────────────────────────
@dataclass
class PlatformAccount:
    platform:      str
    name:          str
    handle:        str
    connected:     bool  = False
    access_token:  str   = ""
    refresh_token: str   = ""
    expires_at:    float = 0
    followers:     int   = 0
    following:     int   = 0
    posts_count:   int   = 0
    avatar_url:    str   = ""
    last_sync:     float = 0

@dataclass
class Post:
    id:          str
    platforms:   list
    content:     str
    media:       list       = field(default_factory=list)
    hashtags:    list       = field(default_factory=list)
    scheduled:   str        = ""      # ISO datetime or ""
    status:      str        = "draft" # draft, scheduled, publishing, published, failed
    created_at:  str        = ""
    published_at:str        = ""
    results:     dict       = field(default_factory=dict)
    title:       str        = ""      # YouTube title
    tags:        list       = field(default_factory=list)
    link:        str        = ""

@dataclass
class Analytics:
    platform:    str
    date:        str
    impressions: int = 0
    reach:       int = 0
    likes:       int = 0
    comments:    int = 0
    shares:      int = 0
    clicks:      int = 0
    followers:   int = 0

class OmniState:
    def __init__(self):
        self.accounts:    dict  = {}   # platform -> PlatformAccount
        self.posts:       list  = []   # list of Post dicts
        self.analytics:   list  = []   # list of Analytics dicts
        self.scheduler_running: bool = False
        self.content_queue:deque = deque(maxlen=50)
        self.notifications:deque = deque(maxlen=100)
        self.ai_suggestions:list = []

STATE = OmniState()
CLIENTS = set()

# ── Settings ───────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "auto_schedule": False,
    "best_times": {
        "facebook":  ["09:00", "13:00", "17:00"],
        "instagram": ["08:00", "12:00", "19:00"],
        "tiktok":    ["07:00", "14:00", "21:00"],
        "youtube":   ["15:00", "20:00"],
        "pinterest": ["14:00", "21:00"],
        "twitter":   ["09:00", "12:00", "18:00"],
    },
    "default_hashtags": {
        "facebook":  [],
        "instagram": ["#reels", "#instagood", "#viral"],
        "tiktok":    ["#fyp", "#viral", "#trending"],
        "youtube":   [],
        "pinterest": [],
        "twitter":   [],
    },
    "ai_enabled": False,
    "anthropic_api_key": "",
    "watermark_text": "",
    "timezone": "America/Toronto",
    "oauth": {
        "facebook":  {"app_id": "", "app_secret": ""},
        "instagram": {"app_id": "", "app_secret": ""},
        "tiktok":    {"client_key": "", "client_secret": ""},
        "youtube":   {"client_id": "", "client_secret": ""},
        "pinterest": {"app_id": "", "app_secret": ""},
        "twitter":   {"api_key": "", "api_secret": "", "bearer_token": ""},
    },
    "genia": {
        "enabled":          False,
        "api_url":          "https://api.genia.social",
        "api_token":        "",
        "auto_publish":     False,
        "platforms":        ["instagram", "facebook", "tiktok"],
        "default_hashtags": ["#metal", "#underground", "#GIaUnderground"],
        "poll_seconds":     300,
        "lookback_hours":   24,
        "credit_text":      "via GIa Underground 🤘 https://genia.social",
    }
}

def load_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
    except UnicodeDecodeError:
        # Fichier ecrit par une version qui ne precisait pas l'encodage : sous
        # Windows open() retombe sur cp1252. On le relit ainsi une fois, puis
        # la prochaine sauvegarde le remet en UTF-8.
        log.warning("[SETTINGS] fichier non-UTF-8, relecture en cp1252")
        with open(SETTINGS_FILE, "r", encoding="cp1252") as f:
            s = json.load(f)
    # Merge with defaults for new keys
    for k, v in DEFAULT_SETTINGS.items():
        if k not in s:
            s[k] = v
    return s

def save_settings(settings: dict):
    # encoding explicite : sans lui, Windows ecrit en cp1252 et le moindre
    # accent — nom de Page, « Quebec » — rend le fichier illisible en UTF-8,
    # tandis qu'un emoji fait echouer la sauvegarde.
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

SETTINGS = load_settings()

# ── Posts persistence ──────────────────────────────────────────────────────
def load_posts() -> list:
    if not os.path.exists(POSTS_FILE):
        return []
    with open(POSTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_posts():
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(STATE.posts, f, ensure_ascii=False, indent=2)

def load_analytics() -> list:
    if not os.path.exists(ANALYTICS_FILE):
        return []
    with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_analytics():
    with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
        json.dump(STATE.analytics, f, ensure_ascii=False, indent=2)

STATE.posts     = load_posts()
STATE.analytics = load_analytics()
# Load persisted social accounts from settings
STATE.accounts  = SETTINGS.get("accounts", {})

# ── Platform connectors ────────────────────────────────────────────────────
def get_oauth_url(platform: str) -> str:
    """Returns OAuth authorization URL for a platform"""
    cfg = SETTINGS.get("oauth", {}).get(platform, {})
    callback = f"http://localhost:{AUTH_PORT}/oauth/callback/{platform}"

    if platform in ("facebook", "instagram"):
        app_id = cfg.get("app_id", "")
        if not app_id:
            return ""
        # pages_show_list is what makes /me/accounts return anything at all —
        # without it the page lookup comes back empty and nothing can publish.
        scope = ("pages_show_list,pages_manage_posts,pages_read_engagement,"
                 "business_management,instagram_basic,instagram_content_publish")
        return (f"https://www.facebook.com/v18.0/dialog/oauth"
                f"?client_id={app_id}&redirect_uri={urllib.parse.quote(callback)}"
                f"&scope={scope}&response_type=code")

    elif platform == "tiktok":
        client_key = cfg.get("client_key", "")
        if not client_key:
            return ""
        return (f"https://www.tiktok.com/v2/auth/authorize/"
                f"?client_key={client_key}&response_type=code"
                f"&scope=user.info.basic,video.publish"
                f"&redirect_uri={urllib.parse.quote(callback)}")

    elif platform == "youtube":
        client_id = cfg.get("client_id", "")
        if not client_id:
            return ""
        scope = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
        # prompt=consent : sans lui, Google ne renvoie le refresh_token qu'a la
        # toute premiere autorisation. Une reconnexion ulterieure repartirait
        # sans lui, et l'acces expirerait au bout d'une heure sans recours.
        return (f"https://accounts.google.com/o/oauth2/v2/auth"
                f"?client_id={client_id}&redirect_uri={urllib.parse.quote(callback)}"
                f"&response_type=code&scope={urllib.parse.quote(scope)}"
                f"&access_type=offline&prompt=consent")

    elif platform == "pinterest":
        app_id = cfg.get("app_id", "")
        if not app_id:
            return ""
        return (f"https://www.pinterest.com/oauth/"
                f"?client_id={app_id}&redirect_uri={urllib.parse.quote(callback)}"
                f"&response_type=code&scope=boards:read,pins:write,user_accounts:read")

    elif platform == "twitter":
        return "https://developer.twitter.com/en/portal/dashboard"

    return ""

async def publish_post(post: dict) -> dict:
    """Publish a post to all selected platforms"""
    results = {}
    for platform in post.get("platforms", []):
        try:
            result = await _publish_to_platform(platform, post)
            results[platform] = result
            # Sans le motif, un « error » dans le journal n'apprend rien et il
            # faut aller fouiller omnipost_posts.json pour savoir quoi corriger.
            if result.get("status") == "published":
                log.info(f"[PUBLISH] {platform}: publie {result.get('url') or result.get('id') or ''}")
            else:
                log.error(f"[PUBLISH] {platform}: {result.get('status')} — "
                          f"{result.get('error') or result.get('message') or 'sans motif'}")
        except Exception as e:
            results[platform] = {"status": "error", "error": str(e)}
            log.error(f"[PUBLISH] {platform} error: {e}")
    return results

async def _publish_to_platform(platform: str, post: dict) -> dict:
    """Platform-specific publish logic"""
    acc = STATE.accounts.get(platform)
    if not acc or not acc.get("connected"):
        return {"status": "error", "error": "Not connected"}

    token = acc.get("access_token", "")
    media = post.get("media", [])

    # The pipeline builds a caption per platform, then only the primary one was
    # ever published — every network got the Instagram wording. Prefer the
    # platform's own caption, which already carries its link and hashtags.
    per_platform = (post.get("captions") or {}).get(platform)
    if per_platform:
        full_text = per_platform.strip()
    else:
        content = post.get("content", "")
        hashtags = " ".join(post.get("hashtags", []))
        full_text = f"{content}\n\n{hashtags}".strip()

    if platform == "facebook":
        return await _post_facebook(token, full_text, media, page_id=acc.get("page_id"),
                                    link=post.get("link", ""))
    elif platform == "instagram":
        return await _post_instagram(token, full_text, media,
                                     ig_id=acc.get("ig_id"), page_id=acc.get("page_id"))
    elif platform == "tiktok":
        return await _post_tiktok(token, full_text, media)
    elif platform == "youtube":
        return await _post_youtube(token, post.get("title", content[:100]), full_text, media)
    elif platform == "pinterest":
        return await _post_pinterest(token, full_text, media, post.get("link", ""))
    elif platform == "twitter":
        return await _post_twitter(post, full_text, media, link=post.get("link", ""))
    return {"status": "error", "error": "Unknown platform"}

# ── Media / HTTP helpers ───────────────────────────────────────────────────
FB_API       = "https://graph.facebook.com/v18.0"
FB_VIDEO_API = "https://graph-video.facebook.com/v18.0"   # video uploads use this host
VIDEO_EXTS   = (".mp4", ".mov", ".webm", ".m4v")


def _is_remote(item: str) -> bool:
    return isinstance(item, str) and item.lower().startswith(("http://", "https://"))


def _is_video(item: str) -> bool:
    return str(item).split("?")[0].lower().endswith(VIDEO_EXTS)


def _local_path(item: str) -> Optional[str]:
    """Resolve a media entry to a readable local file, or None.

    The dashboard used to hand us `blob:` URLs, which only exist inside the
    browser tab. Those are rejected here rather than failing deep inside an
    API call with an unreadable error.
    """
    if not isinstance(item, str) or _is_remote(item) or item.startswith("blob:"):
        return None
    for cand in (item, os.path.join(MEDIA_DIR, os.path.basename(item))):
        if os.path.isfile(cand):
            return cand
    return None


def _media_error(item: str) -> str:
    if isinstance(item, str) and item.startswith("blob:"):
        return ("Media was not uploaded to the backend (blob: URL). "
                "Re-attach the file in the dashboard so it is saved to disk first.")
    return f"Media not found: {item}"


def _multipart(fields: dict, files: list) -> tuple:
    """Build a multipart/form-data body. files: [(name, filename, bytes, ctype)]"""
    boundary = "----OmniPost" + secrets.token_hex(16)
    body = bytearray()
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    for name, filename, blob, ctype in files:
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{name}"; '
                 f'filename="{filename}"\r\n').encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += blob + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _request_json(url: str, data=None, headers=None, method="GET", timeout=30) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode(errors="ignore")
    return json.loads(raw) if raw.strip() else {}


def _post_form(url: str, fields: dict, timeout=30) -> dict:
    return _request_json(url, data=urllib.parse.urlencode(fields).encode(),
                         method="POST", timeout=timeout)


def _upload_file(url: str, fields: dict, path: str, field_name: str, timeout=120) -> dict:
    with open(path, "rb") as fh:
        blob = fh.read()
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    body, content_type = _multipart(fields, [(field_name, os.path.basename(path), blob, ctype)])
    return _request_json(url, data=body, headers={"Content-Type": content_type},
                         method="POST", timeout=timeout)


def _http_error(e: urllib.error.HTTPError) -> dict:
    return {"status": "error", "error": f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}"}


async def _post_facebook(token: str, text: str, media: list, page_id: str = None,
                         link: str = "") -> dict:
    """Post to a Facebook page — link card, video, photo, album or plain text.

    Uses page_id if provided (token = PAGE token), otherwise looks it up via
    /me/accounts (token = USER token).
    """
    try:
        # If page_id not provided, lookup via user token
        if not page_id:
            pages = _request_json(f"{FB_API}/me/accounts?access_token={token}", timeout=10)
            if not pages.get("data"):
                return {"status": "error", "error": "No pages found"}
            page = pages["data"][0]
            page_token = page["access_token"]
            page_id    = page["id"]
        else:
            page_token = token  # caller already gave us the page token

        videos = [m for m in (media or []) if _is_video(m)]
        photos = [m for m in (media or []) if not _is_video(m)]

        # ── A link with no media of its own: let Facebook build the preview
        # card from the destination's Open Graph tags. Sending the URL only in
        # the message body gives a bare text post with no thumbnail.
        if link and not videos and not photos:
            result = _post_form(f"{FB_API}/{page_id}/feed",
                                {"message": text, "link": link,
                                 "access_token": page_token}, timeout=60)
            post_id = result.get("post_id") or result.get("id", "")
            return {"status": "published", "id": post_id,
                    "url": f"https://www.facebook.com/{post_id.replace('_', '/posts/')}"}

        # ── Video wins: the pipeline renders a 9:16 clip and it used to be
        # filtered out here, so every automated post went out as bare text.
        if videos:
            item = videos[0]
            fields = {"description": text, "access_token": page_token}
            if _is_remote(item):
                result = _post_form(f"{FB_VIDEO_API}/{page_id}/videos",
                                    {**fields, "file_url": item}, timeout=300)
            else:
                path = _local_path(item)
                if not path:
                    return {"status": "error", "error": _media_error(item)}
                result = _upload_file(f"{FB_VIDEO_API}/{page_id}/videos", fields,
                                      path, "source", timeout=600)
            vid = result.get("id", "")
            return {"status": "published", "id": vid,
                    "url": f"https://www.facebook.com/{page_id}/videos/{vid}" if vid else ""}

        # ── No media: plain text post on the feed.
        if not photos:
            result = _post_form(f"{FB_API}/{page_id}/feed",
                                {"message": text, "access_token": page_token})

        # ── One photo: /photos takes the caption directly.
        elif len(photos) == 1:
            item = photos[0]
            if _is_remote(item):
                result = _post_form(f"{FB_API}/{page_id}/photos",
                                    {"url": item, "caption": text,
                                     "access_token": page_token}, timeout=60)
            else:
                path = _local_path(item)
                if not path:
                    return {"status": "error", "error": _media_error(item)}
                result = _upload_file(f"{FB_API}/{page_id}/photos",
                                      {"caption": text, "access_token": page_token},
                                      path, "source")

        # ── Several photos: upload each unpublished, then attach them to one post.
        else:
            media_fbids = []
            for item in photos[:10]:
                fields = {"published": "false", "access_token": page_token}
                if _is_remote(item):
                    up = _post_form(f"{FB_API}/{page_id}/photos",
                                    {**fields, "url": item}, timeout=60)
                else:
                    path = _local_path(item)
                    if not path:
                        return {"status": "error", "error": _media_error(item)}
                    up = _upload_file(f"{FB_API}/{page_id}/photos", fields, path, "source")
                if up.get("id"):
                    media_fbids.append({"media_fbid": up["id"]})
            if not media_fbids:
                return {"status": "error", "error": "No photo could be uploaded"}
            result = _post_form(f"{FB_API}/{page_id}/feed", {
                "message": text,
                "attached_media": json.dumps(media_fbids),
                "access_token": page_token,
            }, timeout=60)

        post_id = result.get("post_id") or result.get("id", "")
        return {"status": "published", "id": post_id,
                "url": f"https://www.facebook.com/{post_id.replace('_', '/posts/')}"}
    except urllib.error.HTTPError as e:
        return _http_error(e)
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def _post_instagram(token: str, caption: str, media: list,
                          ig_id: str = None, page_id: str = None) -> dict:
    """Publish a photo or reel to an Instagram Business account.

    Instagram's Content Publishing API only ingests media it can fetch itself,
    so the entry must be a public http(s) URL — a local file can never work.
    """
    try:
        if not media:
            return {"status": "error", "error": "Instagram requires at least one image/video"}

        item = media[0]
        if not _is_remote(item):
            return {"status": "error", "error": (
                "Instagram only accepts a publicly reachable https URL, not a local file. "
                "Host the media (e.g. upload it to genia.social) and use that URL."
            )}

        # instagram_business_account is a field on the Page, not on /me —
        # querying /me always came back empty.
        if not ig_id:
            if not page_id:
                return {"status": "error",
                        "error": "Instagram not connected — reconnect it in the dashboard"}
            data = _request_json(f"{FB_API}/{page_id}?" + urllib.parse.urlencode({
                "fields": "instagram_business_account", "access_token": token}), timeout=15)
            ig_id = (data.get("instagram_business_account") or {}).get("id")
        if not ig_id:
            return {"status": "error", "error": "No Instagram Business account linked to the Page"}

        # Step 1: create the media container
        fields = {"caption": caption, "access_token": token}
        if _is_video(item):
            fields.update({"media_type": "REELS", "video_url": item})
        else:
            fields["image_url"] = item
        container = _post_form(f"{FB_API}/{ig_id}/media", fields, timeout=60)
        container_id = container.get("id")
        if not container_id:
            return {"status": "error", "error": f"Container not created: {container}"}

        # Step 2: wait for Instagram to finish ingesting it. Publishing a
        # container that is still IN_PROGRESS fails with an opaque error, and
        # reels routinely take tens of seconds.
        deadline = time.time() + 300
        while time.time() < deadline:
            state = _request_json(
                f"{FB_API}/{container_id}?fields=status_code,status&access_token={token}",
                timeout=15)
            code = state.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                return {"status": "error",
                        "error": f"Media processing {code}: {state.get('status', '')}"}
            await asyncio.sleep(5)
        else:
            return {"status": "error", "error": "Timed out waiting for Instagram to process the media"}

        # Step 3: publish
        result = _post_form(f"{FB_API}/{ig_id}/media_publish",
                            {"creation_id": container_id, "access_token": token}, timeout=60)
        return {"status": "published", "id": result.get("id")}
    except urllib.error.HTTPError as e:
        return _http_error(e)
    except Exception as e:
        return {"status": "error", "error": str(e)}

TT_API    = "https://open.tiktokapis.com/v2"
TT_CHUNK  = 32 * 1024 * 1024      # TikTok wants 5–64 MB per chunk
TT_SINGLE = 64 * 1024 * 1024      # below this the file goes up in one piece


def _tiktok_upload(upload_url: str, path: str, size: int, chunk: int, total: int):
    """PUT each chunk with the Content-Range TikTok expects."""
    with open(path, "rb") as fh:
        for i in range(total):
            start = i * chunk
            # The last chunk absorbs the remainder rather than leaving a runt
            # below TikTok's 5 MB floor.
            length = (size - start) if i == total - 1 else chunk
            fh.seek(start)
            blob = fh.read(length)
            req = urllib.request.Request(upload_url, data=blob, method="PUT", headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(blob)),
                "Content-Range": f"bytes {start}-{start + len(blob) - 1}/{size}",
            })
            with urllib.request.urlopen(req, timeout=600):
                pass


async def _post_tiktok(token: str, text: str, media: list) -> dict:
    """Publish a video through the Content Posting API.

    Three steps: init returns an upload URL, the file is PUT in chunks, then
    the publish status is polled. Previously this returned "manual" and posted
    nothing, while tiktok sat in the pipeline's default platform list.
    """
    try:
        videos = [m for m in (media or []) if _is_video(m)]
        if not videos:
            return {"status": "error", "error": "TikTok requires a video file"}
        item = videos[0]
        cfg = SETTINGS.get("oauth", {}).get("tiktok", {})
        privacy = cfg.get("privacy_level") or "PUBLIC_TO_EVERYONE"
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json; charset=UTF-8"}
        post_info = {"title": (text or "")[:2200], "privacy_level": privacy}

        if _is_remote(item):
            source_info = {"source": "PULL_FROM_URL", "video_url": item}
            path = size = chunk = total = None
        else:
            path = _local_path(item)
            if not path:
                return {"status": "error", "error": _media_error(item)}
            size = os.path.getsize(path)
            if size <= TT_SINGLE:
                chunk, total = size, 1
            else:
                chunk = TT_CHUNK
                total = max(1, size // chunk)
            source_info = {"source": "FILE_UPLOAD", "video_size": size,
                           "chunk_size": chunk, "total_chunk_count": total}

        init = await asyncio.to_thread(
            _request_json, f"{TT_API}/post/publish/video/init/",
            json.dumps({"post_info": post_info, "source_info": source_info}).encode(),
            headers, "POST", 60)
        err = (init.get("error") or {})
        if err.get("code") not in (None, "ok"):
            hint = ""
            if "privacy" in str(err.get("message", "")).lower():
                hint = (" — une app non auditée par TikTok ne peut publier "
                        "qu'en SELF_ONLY ; règle privacy_level en conséquence.")
            return {"status": "error", "error": f"{err.get('message') or err.get('code')}{hint}"}
        data = init.get("data") or {}
        publish_id = data.get("publish_id")
        if not publish_id:
            return {"status": "error", "error": f"init sans publish_id: {init}"}

        if path:
            await asyncio.to_thread(_tiktok_upload, data.get("upload_url"),
                                    path, size, chunk, total)

        # TikTok processes asynchronously; report only once it is really out.
        deadline = time.time() + 300
        etat = "PROCESSING"
        while time.time() < deadline:
            st = await asyncio.to_thread(
                _request_json, f"{TT_API}/post/publish/status/fetch/",
                json.dumps({"publish_id": publish_id}).encode(), headers, "POST", 30)
            etat = ((st.get("data") or {}).get("status") or "").upper()
            if etat in ("PUBLISH_COMPLETE", "FAILED"):
                break
            await asyncio.sleep(5)

        if etat == "FAILED":
            return {"status": "error", "error": f"TikTok a rejeté la publication ({publish_id})"}
        if etat != "PUBLISH_COMPLETE":
            return {"status": "pending", "id": publish_id,
                    "error": "toujours en traitement chez TikTok après 5 min"}
        return {"status": "published", "id": publish_id}
    except urllib.error.HTTPError as e:
        return _http_error(e)
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def _post_youtube(token: str, title: str, description: str, media: list) -> dict:
    """Upload a video to YouTube via multipart Data API v3."""
    import urllib.request, urllib.error
    try:
        if not media:
            return {"status": "error", "error": "YouTube requires a video file"}
        video_path = media[0]
        if not os.path.isfile(video_path):
            return {"status": "error", "error": "video not found: " + str(video_path)}

        def _do_upload():
            with open(video_path, "rb") as f:
                vbytes = f.read()
            boundary = "----GIaUploadBoundary42"
            metadata = json.dumps({
                "snippet": {
                    "title": (title or "GIa Underground")[:100],
                    "description": (description or "")[:4900],
                    "categoryId": "10",
                    "tags": ["metal", "underground", "GIaUnderground", "shorts"],
                },
                "status": {
                    "privacyStatus": "public",
                    "selfDeclaredMadeForKids": False,
                },
            }).encode("utf-8")
            CRLF = bytes([13, 10])
            DASH = b"--"
            bnd = boundary.encode()
            body = (
                DASH + bnd + CRLF +
                b"Content-Type: application/json; charset=UTF-8" + CRLF + CRLF +
                metadata + CRLF +
                DASH + bnd + CRLF +
                b"Content-Type: video/mp4" + CRLF + CRLF +
                vbytes + CRLF +
                DASH + bnd + DASH + CRLF
            )
            req = urllib.request.Request(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status",
                data=body,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "multipart/related; boundary=" + boundary,
                    "Content-Length": str(len(body)),
                },
            )
            return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        try:
            r = await loop.run_in_executor(None, _do_upload)
        except urllib.error.HTTPError as e:
            # On 401, try to refresh the access token and retry once
            if e.code == 401:
                try:
                    new_tok = _refresh_youtube_token()
                    if new_tok:
                        # Retry with new token (rebuild the closure)
                        # nonlocal token (param overwrite)
                        token = new_tok
                        r = await loop.run_in_executor(None, _do_upload)
                    else:
                        err_body = e.read().decode(errors="ignore")[:300]
                        return {"status": "error", "error": "HTTP 401 refresh failed: " + err_body}
                except Exception as e2:
                    return {"status": "error", "error": "Refresh failed: " + str(e2)}
            else:
                err_body = e.read().decode(errors="ignore")[:300]
                return {"status": "error", "error": "HTTP " + str(e.code) + ": " + err_body}

        vid = r.get("id")
        return {
            "status": "published",
            "id": vid,
            "url": "https://www.youtube.com/watch?v=" + str(vid),
            "shorts_url": "https://www.youtube.com/shorts/" + str(vid),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def _post_pinterest(token: str, description: str, media: list, link: str) -> dict:
    """
    Phase 5 — real Pinterest v5 publisher.
    Requires:
      - oauth.pinterest.access_token (Bearer)
      - oauth.pinterest.pinterest_board_id (target board, e.g. "GIa Underground")
    Pinterest pins MUST have an image (image_url) — text-only is not supported.
    Docs: https://developers.pinterest.com/docs/api/v5/pins-create
    See PINTEREST_SETUP.md for OAuth + board setup.
    """
    cfg = SETTINGS.get("oauth", {}).get("pinterest", {}) or {}
    bearer = token or cfg.get("access_token", "")
    board_id = cfg.get("pinterest_board_id", "")
    if not bearer:
        return {"status": "error", "error": "Pinterest access_token not configured (see PINTEREST_SETUP.md)"}
    if not board_id:
        return {"status": "error", "error": "Pinterest pinterest_board_id not configured (see PINTEREST_SETUP.md)"}
    if not media:
        return {"status": "error", "error": "Pinterest pins require an image (no media provided)"}

    image_url = media[0]
    title = (description or "GIa Underground")[:100]
    payload = {
        "board_id": board_id,
        "title": title,
        "description": (description or "")[:500],
        "link": link or "https://genia.social",
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
    }
    try:
        req = urllib.request.Request(
            "https://api.pinterest.com/v5/pins",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            result = json.loads(r.read().decode())
        return {
            "status": "published",
            "id": result.get("id"),
            "url": f"https://www.pinterest.com/pin/{result.get('id')}/" if result.get("id") else None,
        }
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err_body = ""
        return {"status": "error", "error": f"HTTP {e.code}: {err_body[:300]}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def _pe(value) -> str:
    """Percent-encode per RFC 5849 §3.6."""
    return urllib.parse.quote(str(value), safe="~")


def _oauth1_header(method: str, url: str, cfg: dict, signed_params: dict = None) -> str:
    """OAuth 1.0a user-context Authorization header (HMAC-SHA1).

    Only oauth_* and query parameters are signed. Bodies that are not
    form-encoded — JSON, multipart — are excluded from the signature base,
    which is what X expects for /2/tweets and media/upload.
    """
    oauth = {
        "oauth_consumer_key":     cfg.get("api_key", ""),
        "oauth_nonce":            secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        str(int(time.time())),
        "oauth_token":            cfg.get("access_token", ""),
        "oauth_version":          "1.0",
    }
    to_sign = {**oauth, **(signed_params or {})}
    param_str = "&".join(f"{_pe(k)}={_pe(v)}" for k, v in sorted(to_sign.items()))
    base = f"{method.upper()}&{_pe(url)}&{_pe(param_str)}"
    key = f'{_pe(cfg.get("api_secret", ""))}&{_pe(cfg.get("access_token_secret", ""))}'.encode()
    oauth["oauth_signature"] = base64.b64encode(
        hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pe(k)}="{_pe(v)}"' for k, v in sorted(oauth.items()))


X_UPLOAD = "https://upload.twitter.com/1.1/media/upload.json"


def _twitter_upload_media(cfg: dict, path: str) -> Optional[str]:
    """Upload one image to X and return its media_id."""
    with open(path, "rb") as fh:
        blob = fh.read()
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    body, content_type = _multipart({}, [("media", os.path.basename(path), blob, ctype)])
    result = _request_json(X_UPLOAD, data=body, method="POST", timeout=120, headers={
        "Content-Type": content_type,
        "Authorization": _oauth1_header("POST", X_UPLOAD, cfg),
    })
    return result.get("media_id_string")


def _twitter_upload_video(cfg: dict, path: str) -> Optional[str]:
    """Chunked INIT/APPEND/FINALIZE upload — the only way X accepts video."""
    size = os.path.getsize(path)
    ctype = mimetypes.guess_type(path)[0] or "video/mp4"

    def form(fields, timeout=60):
        # Form-encoded bodies are part of the OAuth signature base.
        return _request_json(X_UPLOAD, data=urllib.parse.urlencode(fields).encode(),
                             method="POST", timeout=timeout, headers={
                                 "Content-Type": "application/x-www-form-urlencoded",
                                 "Authorization": _oauth1_header("POST", X_UPLOAD, cfg, fields),
                             })

    init = form({"command": "INIT", "total_bytes": str(size),
                 "media_type": ctype, "media_category": "tweet_video"})
    media_id = init.get("media_id_string")
    if not media_id:
        return None

    CHUNK = 4 * 1024 * 1024
    with open(path, "rb") as fh:
        index = 0
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            # APPEND is multipart, so its body stays out of the signature.
            body, content_type = _multipart(
                {"command": "APPEND", "media_id": media_id, "segment_index": str(index)},
                [("media", "chunk", chunk, "application/octet-stream")])
            _request_json(X_UPLOAD, data=body, method="POST", timeout=300, headers={
                "Content-Type": content_type,
                "Authorization": _oauth1_header("POST", X_UPLOAD, cfg),
            })
            index += 1

    done = form({"command": "FINALIZE", "media_id": media_id}, timeout=120)

    # X transcodes asynchronously; attaching the id too early fails the post.
    info = done.get("processing_info") or {}
    deadline = time.time() + 300
    while info.get("state") in ("pending", "in_progress") and time.time() < deadline:
        time.sleep(max(1, int(info.get("check_after_secs", 5))))
        params = {"command": "STATUS", "media_id": media_id}
        status = _request_json(X_UPLOAD + "?" + urllib.parse.urlencode(params), timeout=30,
                               headers={"Authorization": _oauth1_header(
                                   "GET", X_UPLOAD, cfg, params)})
        info = status.get("processing_info") or {}
    if info.get("state") == "failed":
        log.error(f"[X] transcodage échoué: {info.get('error')}")
        return None
    return media_id


X_URL_WEIGHT = 23          # X counts every URL as 23 chars, whatever its length


def _fit_with_link(text: str, link: str, limit: int = 280) -> str:
    """Append the URL and keep it intact, trimming the body instead.

    The returned string can be longer than `limit` in raw characters when the
    URL exceeds 23: X weighs links at a flat 23, so it is the weighted length
    that has to fit, not len().
    """
    if not link or link in text:
        return text[:limit]
    room = limit - X_URL_WEIGHT - 1        # the newline before the link
    body = text.strip()
    if len(body) > room:
        body = body[:max(0, room - 1)].rstrip() + "…"
    return f"{body}\n{link}" if body else link


async def _post_twitter(post: dict, text: str, media: list, link: str = "") -> dict:
    """Post to X. Requires OAuth 1.0a user context — a Bearer token is
    app-only auth and cannot create posts (it always returns 403)."""
    try:
        cfg = SETTINGS.get("oauth", {}).get("twitter", {})
        missing = [k for k in ("api_key", "api_secret", "access_token", "access_token_secret")
                   if not cfg.get(k)]
        if missing:
            return {"status": "error", "error": (
                f"X needs OAuth 1.0a user credentials — missing: {', '.join(missing)}. "
                "A Bearer token is app-only and cannot post; generate an access token "
                "and secret with Read and Write permission in the X developer portal."
            )}

        # X renders its own card from the URL's Open Graph tags, so the link
        # must survive truncation rather than be cut in half by it.
        payload = {"text": _fit_with_link(text, link)}

        # A tweet carries either one video or up to four images, never both.
        media_ids = []
        videos = [m for m in (media or []) if _is_video(m)]
        chosen = videos[:1] if videos else (media or [])[:4]
        for item in chosen:
            path = _local_path(item)
            if not path:
                if _is_remote(item):
                    continue  # X has no fetch-by-URL ingest; skip remote entries
                return {"status": "error", "error": _media_error(item)}
            mid = (await asyncio.to_thread(_twitter_upload_video, cfg, path) if _is_video(item)
                   else await asyncio.to_thread(_twitter_upload_media, cfg, path))
            if mid:
                media_ids.append(mid)
        if media_ids:
            payload["media"] = {"media_ids": media_ids}

        url = "https://api.twitter.com/2/tweets"
        result = _request_json(url, data=json.dumps(payload).encode(), method="POST", timeout=30,
                               headers={
                                   "Content-Type": "application/json",
                                   "Authorization": _oauth1_header("POST", url, cfg),
                               })
        tweet_id = result.get("data", {}).get("id")
        return {"status": "published", "id": tweet_id,
                "url": f"https://x.com/i/status/{tweet_id}" if tweet_id else ""}
    except urllib.error.HTTPError as e:
        return _http_error(e)
    except Exception as e:
        return {"status": "error", "error": str(e)}

# ── AI Content Generator ───────────────────────────────────────────────────
async def generate_content_ai(prompt: str, platform: str, tone: str = "engaging") -> dict:
    """Generate content using Claude API"""
    api_key = SETTINGS.get("anthropic_api_key", "")
    if not api_key:
        return {"error": "Anthropic API key not configured", "content": ""}

    max_chars = PLATFORMS.get(platform, {}).get("max_chars", 500)
    hashtag_count = {"instagram": 20, "tiktok": 10, "twitter": 3, "facebook": 5, "pinterest": 10, "youtube": 15, "linkedin": 5}.get(platform, 10)

    system = f"""You are an expert social media content creator specializing in {platform}.
Generate {tone} content optimized for {platform}.
Keep text under {max_chars} characters.
Include {hashtag_count} relevant hashtags.
Format: First the post text, then hashtags on a new line starting with #.
Never use emojis in hashtags. Use popular and niche hashtags."""

    try:
        data = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": system,
            "messages": [{"role": "user", "content": f"Create a {platform} post about: {prompt}"}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())

        text = result.get("content", [{}])[0].get("text", "")
        lines = text.strip().split("\n")
        content_lines = []
        hashtag_lines = []
        for line in lines:
            if line.strip().startswith("#"):
                hashtag_lines.append(line.strip())
            else:
                content_lines.append(line)

        content  = "\n".join(content_lines).strip()
        hashtags = []
        for hl in hashtag_lines:
            hashtags.extend(hl.split())

        return {"content": content, "hashtags": hashtags[:hashtag_count], "error": ""}
    except Exception as e:
        return {"error": str(e), "content": "", "hashtags": []}

# ── Scheduler ─────────────────────────────────────────────────────────────
async def scheduler_loop():
    """Check for posts to publish every minute"""
    log.info("[SCHEDULER] Démarré")
    while True:
        try:
            now = datetime.now()
            for post in STATE.posts:
                if post.get("status") != "scheduled":
                    continue
                scheduled = post.get("scheduled", "")
                if not scheduled:
                    continue
                try:
                    sched_dt = datetime.fromisoformat(scheduled)
                    if sched_dt <= now:
                        log.info(f"[SCHEDULER] Publication du post {post['id']}")
                        post["status"] = "publishing"
                        await broadcast({"type": "post_status", "id": post["id"], "status": "publishing"})
                        results = await publish_post(post)
                        post["status"] = "published"
                        post["published_at"] = now.isoformat()
                        post["results"] = results
                        save_posts()
                        await broadcast({"type": "post_published", "post": post})
                        add_notification(f"✅ Post publié sur {', '.join(post['platforms'])}", "success")
                except ValueError:
                    pass
        except Exception as e:
            log.error(f"[SCHEDULER] Erreur: {e}")
        await asyncio.sleep(60)

def add_notification(message: str, level: str = "info"):
    STATE.notifications.appendleft({
        "ts":      datetime.now().strftime("%H:%M:%S"),
        "message": message,
        "level":   level,
    })

# ── Analytics ─────────────────────────────────────────────────────────────
def get_analytics_summary() -> dict:
    """Generate analytics summary across all platforms"""
    if not STATE.analytics:
        return {"total_impressions": 0, "total_reach": 0, "total_likes": 0,
                "total_comments": 0, "total_shares": 0, "by_platform": {}}

    summary = {"total_impressions": 0, "total_reach": 0, "total_likes": 0,
               "total_comments": 0, "total_shares": 0, "by_platform": {}}

    for a in STATE.analytics:
        summary["total_impressions"] += a.get("impressions", 0)
        summary["total_reach"]       += a.get("reach", 0)
        summary["total_likes"]       += a.get("likes", 0)
        summary["total_comments"]    += a.get("comments", 0)
        summary["total_shares"]      += a.get("shares", 0)
        p = a.get("platform", "")
        if p not in summary["by_platform"]:
            summary["by_platform"][p] = {"impressions": 0, "likes": 0, "comments": 0}
        summary["by_platform"][p]["impressions"] += a.get("impressions", 0)
        summary["by_platform"][p]["likes"]       += a.get("likes", 0)
        summary["by_platform"][p]["comments"]    += a.get("comments", 0)

    return summary

def generate_demo_analytics():
    """Generate demo analytics data for UI preview"""
    platforms = ["facebook", "instagram", "tiktok", "youtube", "pinterest"]
    for i in range(30):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        for p in platforms:
            import random
            STATE.analytics.append({
                "platform":    p,
                "date":        date,
                "impressions": random.randint(100, 5000),
                "reach":       random.randint(80, 4000),
                "likes":       random.randint(5, 500),
                "comments":    random.randint(0, 50),
                "shares":      random.randint(0, 100),
                "clicks":      random.randint(0, 200),
                "followers":   random.randint(0, 10),
            })

# ── WebSocket ──────────────────────────────────────────────────────────────
async def broadcast(msg: dict):
    if not CLIENTS:
        return
    data = json.dumps(msg)
    dead = set()
    for client in list(CLIENTS):
        try:
            await client.send(data)
        except Exception:
            dead.add(client)
    CLIENTS.difference_update(dead)

def build_state() -> dict:
    return {
        "type":           "state",
        "accounts":       STATE.accounts,
        "posts":          STATE.posts[-50:],
        "notifications":  list(STATE.notifications)[:20],
        "analytics":      get_analytics_summary(),
        "scheduled_count": sum(1 for p in STATE.posts if p.get("status") == "scheduled"),
        "published_count": sum(1 for p in STATE.posts if p.get("status") == "published"),
        "draft_count":    sum(1 for p in STATE.posts if p.get("status") == "draft"),
        "platforms":      PLATFORMS,
        "settings":       {k: v for k, v in SETTINGS.items() if k != "anthropic_api_key"},
    }

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


async def handle_command(ws, msg: dict):
    cmd = msg.get("cmd")

    # ── Media ────────────────────────────────────────────────────────────
    if cmd == "upload_media":
        # The dashboard sends the file's bytes base64-encoded over this socket.
        # Before this existed it only ever sent blob: URLs, which are local to
        # the browser tab, so nothing could actually be published with media.
        name = os.path.basename(msg.get("filename", "") or "upload.bin")
        try:
            blob = base64.b64decode(msg.get("data", ""), validate=True)
        except Exception:
            await ws.send(json.dumps({"type": "media_uploaded", "error": "Invalid base64 payload"}))
            return
        if not blob:
            await ws.send(json.dumps({"type": "media_uploaded", "error": "Empty file"}))
            return
        if len(blob) > MAX_UPLOAD_BYTES:
            await ws.send(json.dumps({"type": "media_uploaded",
                                      "error": f"File too large ({len(blob) // 1048576} MB, max 25 MB)"}))
            return
        stem, ext = os.path.splitext(name)
        safe = "".join(c for c in stem if c.isalnum() or c in "-_")[:40] or "media"
        path = os.path.join(MEDIA_DIR, f"{safe}_{secrets.token_hex(4)}{ext[:10]}")
        with open(path, "wb") as fh:
            fh.write(blob)
        log.info(f"[MEDIA] {path} ({len(blob)} bytes)")
        await ws.send(json.dumps({"type": "media_uploaded", "path": path, "name": name}))
        return

    # ── Posts ────────────────────────────────────────────────────────────
    if cmd == "create_post":
        post = {
            "id":         secrets.token_hex(8),
            "platforms":  msg.get("platforms", []),
            "content":    msg.get("content", ""),
            "media":      msg.get("media", []),
            "hashtags":   msg.get("hashtags", []),
            "scheduled":  msg.get("scheduled", ""),
            "status":     "scheduled" if msg.get("scheduled") else "draft",
            "created_at": datetime.now().isoformat(),
            "published_at": "",
            "results":    {},
            "title":      msg.get("title", ""),
            "tags":       msg.get("tags", []),
            "link":       msg.get("link", ""),
        }
        STATE.posts.insert(0, post)
        save_posts()
        await ws.send(json.dumps({"type": "post_created", "post": post}))
        add_notification(f"📝 Post créé pour {', '.join(post['platforms'])}", "info")

    elif cmd == "publish_now":
        post_id = msg.get("id")
        post = next((p for p in STATE.posts if p["id"] == post_id), None)
        if post:
            post["status"] = "publishing"
            await broadcast({"type": "post_status", "id": post_id, "status": "publishing"})
            results = await publish_post(post)
            post["status"] = "published"
            post["published_at"] = datetime.now().isoformat()
            post["results"] = results
            save_posts()
            await broadcast({"type": "post_published", "post": post})
            add_notification(f"🚀 Publié sur {', '.join(post['platforms'])}", "success")

    elif cmd == "delete_post":
        post_id = msg.get("id")
        STATE.posts = [p for p in STATE.posts if p["id"] != post_id]
        save_posts()
        await ws.send(json.dumps({"type": "post_deleted", "id": post_id}))

    elif cmd == "update_post":
        post_id = msg.get("id")
        post = next((p for p in STATE.posts if p["id"] == post_id), None)
        if post:
            for k in ["content", "hashtags", "scheduled", "media", "title", "tags", "link", "platforms"]:
                if k in msg:
                    post[k] = msg[k]
            if msg.get("scheduled"):
                post["status"] = "scheduled"
            save_posts()
            await ws.send(json.dumps({"type": "post_updated", "post": post}))

    # ── AI Content ───────────────────────────────────────────────────────
    elif cmd == "generate_ai":
        prompt   = msg.get("prompt", "")
        platform = msg.get("platform", "instagram")
        tone     = msg.get("tone", "engaging")
        await ws.send(json.dumps({"type": "ai_generating", "platform": platform}))
        result = await generate_content_ai(prompt, platform, tone)
        await ws.send(json.dumps({"type": "ai_content", "result": result, "platform": platform}))

    # ── Connections ──────────────────────────────────────────────────────
    elif cmd == "connect_platform":
        platform = msg.get("platform")
        url = get_oauth_url(platform)
        if url:
            await ws.send(json.dumps({"type": "oauth_url", "platform": platform, "url": url}))
        else:
            await ws.send(json.dumps({"type": "oauth_error", "platform": platform,
                                       "error": "Configure API keys in Settings first"}))

    elif cmd == "disconnect_platform":
        platform = msg.get("platform")
        if platform in STATE.accounts:
            STATE.accounts[platform]["connected"] = False
            STATE.accounts[platform]["access_token"] = ""
            save_settings(SETTINGS)
        await ws.send(json.dumps({"type": "platform_disconnected", "platform": platform}))

    elif cmd == "get_state":
        await ws.send(json.dumps(build_state()))

    # ── Settings ─────────────────────────────────────────────────────────
    elif cmd == "save_settings":
        for k, v in msg.get("settings", {}).items():
            SETTINGS[k] = v
        save_settings(SETTINGS)
        await ws.send(json.dumps({"type": "settings_saved"}))
        add_notification("⚙️ Paramètres sauvegardés", "info")

    elif cmd == "save_genia":
        # Save GeniA listener config (subset of SETTINGS["genia"])
        cfg = msg.get("genia", {}) or {}
        if "genia" not in SETTINGS:
            SETTINGS["genia"] = {}
        for k, v in cfg.items():
            SETTINGS["genia"][k] = v
        save_settings(SETTINGS)
        await ws.send(json.dumps({"type": "genia_saved", "genia": SETTINGS["genia"]}))
        add_notification(
            "🤘 GeniA listener " + ("activé" if SETTINGS["genia"].get("enabled") else "désactivé"),
            "info",
        )

    elif cmd == "get_genia":
        await ws.send(json.dumps({"type": "genia_config", "genia": SETTINGS.get("genia", {})}))

    # ── Pipeline (fabrication → conversion → approbation → drip) ─────────
    elif cmd == "pipeline_status" and HAS_PIPELINE:
        await ws.send(json.dumps(genia_pipeline.get_status_payload()))

    elif cmd == "pipeline_list" and HAS_PIPELINE:
        phase = msg.get("phase", "converted")
        await ws.send(json.dumps(genia_pipeline.get_list_payload(phase)))

    elif cmd == "pipeline_approve" and HAS_PIPELINE:
        ids = msg.get("ids", "all")
        result = await genia_pipeline.approve(ids, broadcast, add_notification)
        await ws.send(json.dumps({"type": "pipeline_approve_done", **result}))

    elif cmd == "pipeline_reject" and HAS_PIPELINE:
        ids = msg.get("ids", [])
        result = await genia_pipeline.reject(ids, broadcast, add_notification)
        await ws.send(json.dumps({"type": "pipeline_reject_done", **result}))

    elif cmd == "save_oauth":
        platform = msg.get("platform")
        keys     = msg.get("keys", {})
        if platform and keys:
            if "oauth" not in SETTINGS:
                SETTINGS["oauth"] = {}
            # Fusionner, jamais remplacer : le formulaire n'envoie que les
            # champs remplis, donc une affectation directe effacait la cle
            # secrete des qu'on resauvegardait juste l'App ID.
            existant = dict(SETTINGS["oauth"].get(platform) or {})
            existant.update({k: v for k, v in keys.items() if v})
            SETTINGS["oauth"][platform] = existant
            save_settings(SETTINGS)
            log.info(f"[OAUTH] cles {platform} enregistrees: {sorted(existant)}")
            await ws.send(json.dumps({"type": "oauth_saved", "platform": platform}))

    # ── Analytics ────────────────────────────────────────────────────────
    elif cmd == "get_analytics":
        period = msg.get("period", "7d")
        cutoff_days = {"7d": 7, "30d": 30, "90d": 90, "all": 9999}.get(period, 7)
        cutoff = (datetime.now() - timedelta(days=cutoff_days)).strftime("%Y-%m-%d")
        filtered = [a for a in STATE.analytics if a.get("date", "") >= cutoff]
        await ws.send(json.dumps({"type": "analytics_data", "data": filtered, "period": period}))

    elif cmd == "load_demo_analytics":
        STATE.analytics = []
        generate_demo_analytics()
        save_analytics()
        await ws.send(json.dumps({"type": "analytics_loaded", "count": len(STATE.analytics)}))

    # ── Hashtag suggestions ──────────────────────────────────────────────
    elif cmd == "suggest_hashtags":
        topic    = msg.get("topic", "")
        platform = msg.get("platform", "instagram")
        # Popular hashtag suggestions based on topic keywords
        common = {
            "food":    ["#food", "#foodie", "#foodphotography", "#delicious", "#yummy", "#homecooking"],
            "travel":  ["#travel", "#wanderlust", "#travelgram", "#explore", "#adventure", "#vacation"],
            "fitness": ["#fitness", "#workout", "#gym", "#health", "#motivation", "#fitlife"],
            "business":["#business", "#entrepreneur", "#marketing", "#success", "#startup", "#growth"],
            "tech":    ["#tech", "#technology", "#coding", "#developer", "#software", "#innovation"],
            "fashion": ["#fashion", "#style", "#ootd", "#outfit", "#clothing", "#streetstyle"],
            "beauty":  ["#beauty", "#makeup", "#skincare", "#cosmetics", "#beautycare", "#glam"],
            "nature":  ["#nature", "#photography", "#landscape", "#outdoors", "#wildlife", "#earth"],
        }
        platform_tags = {
            "tiktok":    ["#fyp", "#foryou", "#viral", "#trending"],
            "instagram": ["#instagood", "#photooftheday", "#instadaily", "#reels"],
            "youtube":   ["#youtube", "#subscribe", "#youtuber"],
            "pinterest": ["#pinterest", "#pinterestinspired"],
        }
        suggestions = platform_tags.get(platform, [])
        for key, tags in common.items():
            if key in topic.lower():
                suggestions.extend(tags)
        if not suggestions:
            suggestions = ["#viral", "#trending", "#content", "#socialmedia", "#marketing"]
        await ws.send(json.dumps({"type": "hashtag_suggestions", "hashtags": suggestions[:20]}))

    # ── Best time to post ────────────────────────────────────────────────
    elif cmd == "get_best_times":
        platform = msg.get("platform", "instagram")
        times = SETTINGS.get("best_times", {}).get(platform, ["09:00", "17:00"])
        await ws.send(json.dumps({"type": "best_times", "platform": platform, "times": times}))

async def ws_handler(websocket):
    CLIENTS.add(websocket)
    log.info(f"[WS] Client connecté: {websocket.remote_address}")
    try:
        await websocket.send(json.dumps(build_state()))
        async for raw in websocket:
            try:
                msg = json.loads(raw)
                await handle_command(websocket, msg)
            except json.JSONDecodeError:
                pass
    except Exception as e:
        log.debug(f"[WS] Déconnecté: {e}")
    finally:
        CLIENTS.discard(websocket)

# ── Auth / OAuth callback server ───────────────────────────────────────────
def _fb_complete_oauth(platform: str, code: str, callback: str) -> dict:
    """Trade an authorization code for a durable page token.

    Facebook hands back a short-lived user token, which is useless an hour
    later. The chain is: code -> short user token -> long-lived user token ->
    page token (page tokens minted from a long-lived user token do not expire).
    """
    cfg = SETTINGS.get("oauth", {}).get(platform, {})
    app_id, app_secret = cfg.get("app_id", ""), cfg.get("app_secret", "")
    if not app_id or not app_secret:
        return {"error": "App ID / App Secret manquants dans les réglages OAuth"}

    try:
        short = _request_json(f"{FB_API}/oauth/access_token?" + urllib.parse.urlencode({
            "client_id": app_id, "client_secret": app_secret,
            "redirect_uri": callback, "code": code,
        }), timeout=20)
        user_token = short.get("access_token")
        if not user_token:
            return {"error": f"Échange du code refusé: {short}"}

        # Upgrade to a ~60 day user token before deriving page tokens from it.
        longed = _request_json(f"{FB_API}/oauth/access_token?" + urllib.parse.urlencode({
            "grant_type": "fb_exchange_token", "client_id": app_id,
            "client_secret": app_secret, "fb_exchange_token": user_token,
        }), timeout=20)
        user_token = longed.get("access_token", user_token)

        pages = _request_json(
            f"{FB_API}/me/accounts?" + urllib.parse.urlencode({"access_token": user_token}),
            timeout=20)
        data = pages.get("data") or []
        if not data:
            return {"error": ("Aucune Page trouvée. Vérifie que tu administres bien une Page "
                              "et que la permission pages_show_list a été accordée.")}

        page = data[0]
        acc = {
            "connected":    True,
            "platform":     platform,
            "access_token": page.get("access_token", ""),
            "user_token":   user_token,
            "page_id":      page.get("id", ""),
            "page_name":    page.get("name", ""),
            "pages":        [{"id": p.get("id"), "name": p.get("name")} for p in data],
            "name":         page.get("name") or PLATFORMS.get(platform, {}).get("name", platform),
        }

        # instagram_business_account hangs off the Page, never off /me.
        if platform == "instagram":
            link = _request_json(f"{FB_API}/{acc['page_id']}?" + urllib.parse.urlencode({
                "fields": "instagram_business_account", "access_token": acc["access_token"],
            }), timeout=20)
            ig_id = (link.get("instagram_business_account") or {}).get("id")
            if not ig_id:
                return {"error": ("Aucun compte Instagram Business rattaché à la Page "
                                  f"« {acc['page_name']} ». Lie-le dans les paramètres de la Page.")}
            acc["ig_id"] = ig_id

        return acc
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}"}
    except Exception as e:
        return {"error": str(e)}


_CODES_VUS = {}          # code -> corps HTML deja renvoye


def _repondre_http(writer, body: str, content_type: str, cors: str = ""):
    """Reponse HTTP complete.

    Sans Content-Length ni Connection: close, le navigateur ne sait pas ou
    s'arrete le corps, attend la suite, puis rejoue la requete — et le second
    appel arrive avec un code OAuth deja consomme.
    """
    corps = body.encode("utf-8")
    entetes = (
        "HTTP/1.1 200 OK\r\n"
        f"Content-Type: {content_type}; charset=utf-8\r\n"
        f"Content-Length: {len(corps)}\r\n"
        "Connection: close\r\n"
        f"{cors}\r\n"
    ).encode("utf-8")
    writer.write(entetes + corps)


GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
TT_TOKEN     = "https://open.tiktokapis.com/v2/oauth/token/"


def _tiktok_complete_oauth(platform: str, code: str, callback: str) -> dict:
    """Echange le code TikTok contre un acces, un refresh et l'open_id."""
    cfg = SETTINGS.get("oauth", {}).get(platform, {})
    if not cfg.get("client_key") or not cfg.get("client_secret"):
        return {"error": "Client Key / Client Secret manquants dans les reglages OAuth"}
    try:
        # TikTok attend un corps form-encode, pas du JSON, et un code decode.
        rep = _post_form(TT_TOKEN, {
            "client_key": cfg["client_key"], "client_secret": cfg["client_secret"],
            "code": urllib.parse.unquote(code), "grant_type": "authorization_code",
            "redirect_uri": callback,
        }, timeout=30)
        if rep.get("error"):
            return {"error": f"{rep.get('error')}: {rep.get('error_description', '')}"}
        acces = rep.get("access_token")
        if not acces:
            return {"error": f"Echange du code refuse: {rep}"}

        nom = ""
        try:
            info = _request_json(
                "https://open.tiktokapis.com/v2/user/info/?fields=display_name",
                headers={"Authorization": f"Bearer {acces}"}, timeout=20)
            nom = (((info.get("data") or {}).get("user") or {}).get("display_name")) or ""
        except Exception as e:               # ne jamais casser la connexion ici
            log.warning(f"[TIKTOK] nom de compte illisible: {e}")

        return {
            "connected": True, "platform": platform,
            "access_token": acces,
            "refresh_token": rep.get("refresh_token", ""),
            "open_id": rep.get("open_id", ""),
            "display_name": nom,
            "name": nom or PLATFORMS.get(platform, {}).get("name", platform),
        }
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}"}
    except Exception as e:
        return {"error": str(e)}



def _refresh_youtube_token():
    """Rejoue le refresh_token pour obtenir un acces frais.

    Etait appelee dans _post_youtube sur un 401 sans avoir jamais ete
    definie : la moindre expiration levait un NameError au lieu de renouveler.
    """
    cfg = SETTINGS.get("oauth", {}).get("youtube", {})
    acc = STATE.accounts.get("youtube") or {}
    refresh = acc.get("refresh_token") or cfg.get("refresh_token", "")
    if not (refresh and cfg.get("client_id") and cfg.get("client_secret")):
        log.error("[YOUTUBE] pas de refresh_token — reconnecter la plateforme")
        return ""
    try:
        rep = _post_form(GOOGLE_TOKEN, {
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        }, timeout=30)
    except urllib.error.HTTPError as e:
        log.error(f"[YOUTUBE] refus du refresh: {e.read().decode(errors='ignore')[:200]}")
        return ""
    jeton = rep.get("access_token", "")
    if jeton:
        acc["access_token"] = jeton
        STATE.accounts["youtube"] = acc
        SETTINGS["accounts"] = STATE.accounts
        save_settings(SETTINGS)
        log.info("[YOUTUBE] acces renouvele")
    return jeton


def _google_complete_oauth(platform: str, code: str, callback: str) -> dict:
    """Echange le code Google contre un acces et un refresh_token."""
    cfg = SETTINGS.get("oauth", {}).get(platform, {})
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        return {"error": "Client ID / Client Secret manquants dans les reglages OAuth"}
    try:
        jetons = _post_form(GOOGLE_TOKEN, {
            "code": code, "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"], "redirect_uri": callback,
            "grant_type": "authorization_code",
        }, timeout=30)
        acces = jetons.get("access_token")
        if not acces:
            return {"error": f"Echange du code refuse: {jetons}"}

        nom = ""
        try:
            ch = _request_json(
                "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                headers={"Authorization": f"Bearer {acces}"}, timeout=20)
            items = ch.get("items") or []
            if items:
                nom = (items[0].get("snippet") or {}).get("title", "")
        except Exception as e:               # la chaine ne doit pas casser ici
            log.warning(f"[YOUTUBE] nom de chaine illisible: {e}")

        return {
            "connected": True, "platform": platform,
            "access_token": acces,
            "refresh_token": jetons.get("refresh_token", ""),
            "channel_name": nom,
            "name": nom or PLATFORMS.get(platform, {}).get("name", platform),
        }
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}"}
    except Exception as e:
        return {"error": str(e)}


async def auth_handler(reader, writer):
    """Handles OAuth callbacks from social platforms"""
    try:
        request = await reader.read(4096)
        req_str = request.decode(errors="ignore")
        path    = req_str.split(" ")[1] if " " in req_str else "/"

        cors = "Access-Control-Allow-Origin: *\r\n"

        if path.startswith("/oauth/callback/"):
            platform = path.split("/oauth/callback/")[1].split("?")[0]
            params   = {}
            if "?" in path:
                qs = path.split("?", 1)[1]
                params = dict(urllib.parse.parse_qsl(qs))

            code  = params.get("code", "")
            error = params.get("error", "")

            if code and code in _CODES_VUS:
                # Un code OAuth ne vaut qu'une fois. Si le navigateur rejoue
                # l'appel, on rend la meme page plutot que de redemander a
                # Facebook un echange qu'il refusera.
                log.info(f"[OAUTH] Code deja traite pour {platform}, rejeu ignore")
                _repondre_http(writer, _CODES_VUS[code], "text/html", cors)
                await writer.drain()
                writer.close()
                return

            if code:
                log.info(f"[OAUTH] Code reçu pour {platform}")
                # The code is worthless on its own — it has to be traded for a
                # token before anything can be published. Skipping this step is
                # what made the dashboard show "connecté" while every publish
                # failed with an empty access_token.
                callback = f"http://localhost:{AUTH_PORT}/oauth/callback/{platform}"
                if platform in ("facebook", "instagram"):
                    acc = await asyncio.to_thread(_fb_complete_oauth, platform, code, callback)
                elif platform == "youtube":
                    acc = await asyncio.to_thread(_google_complete_oauth, platform, code, callback)
                elif platform == "tiktok":
                    acc = await asyncio.to_thread(_tiktok_complete_oauth, platform, code, callback)
                else:
                    acc = {"error": f"Échange de token non implémenté pour {platform}"}

                if acc.get("error"):
                    err = acc["error"]
                    log.error(f"[OAUTH] {platform}: {err}")
                    STATE.accounts.setdefault(platform, {})["connected"] = False
                    add_notification(f"❌ {platform} : {err}", "error")
                    asyncio.create_task(broadcast({
                        "type": "oauth_error", "platform": platform, "error": err,
                    }))
                    body = f"""<html><body style="background:#0f0f13;color:#ff4d6a;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:24px">
                        <div style="text-align:center;max-width:560px"><h2>❌ Connexion {platform} échouée</h2>
                        <p style="color:#9090a8;font-size:14px;line-height:1.5">{err}</p></div></body></html>"""
                else:
                    STATE.accounts[platform] = acc
                    SETTINGS["accounts"] = STATE.accounts
                    save_settings(SETTINGS)          # survive a restart
                    log.info(f"[OAUTH] {platform} connecté — page {acc.get('page_name')} ({acc.get('page_id')})")
                    asyncio.create_task(broadcast({
                        "type":     "platform_connected",
                        "platform": platform,
                        "account":  acc,
                    }))
                    add_notification(f"✅ {PLATFORMS.get(platform,{}).get('name',platform)} connecté !", "success")
                    body = f"""<html><body style="background:#0f0f13;color:#3dffb4;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
                        <div style="text-align:center"><h2>✅ {platform.title()} connecté !</h2>
                        <p style="color:#9090a8">Page : {acc.get('page_name','')}</p>
                        <p style="color:#9090a8">Tu peux fermer cette fenêtre.</p>
                        <script>window.close();</script></div></body></html>"""
            else:
                body = f"""<html><body style="background:#0f0f13;color:#ff4d6a;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
                    <div style="text-align:center"><h2>❌ Erreur: {error}</h2>
                    <script>window.close();</script></div></body></html>"""

            if code:
                _CODES_VUS[code] = body
                if len(_CODES_VUS) > 50:
                    _CODES_VUS.pop(next(iter(_CODES_VUS)))
            _repondre_http(writer, body, "text/html", cors)

        else:
            body = json.dumps({"status": "OmniPost Auth Server", "version": "1.0.0"})
            _repondre_http(writer, body, "application/json", cors)

        await writer.drain()
    except Exception as e:
        log.debug(f"[AUTH] Erreur: {e}")
    finally:
        writer.close()

DASHBOARD_FILE = "omnipost_dashboard.html"


def ouvrir_dashboard():
    """Ouvre le tableau de bord au demarrage.

    Il ne s'ouvrait pas tout seul, et un serveur qui tourne sans interface
    donne l'impression que rien ne demarre. Desactivable par le reglage
    ouvrir_dashboard.
    """
    if not SETTINGS.get("ouvrir_dashboard", True):
        return
    chemin = os.path.abspath(DASHBOARD_FILE)
    if not os.path.isfile(chemin):
        log.warning(f"[UI] {DASHBOARD_FILE} introuvable a cote de omnipost.py")
        return
    try:
        import webbrowser
        webbrowser.open(pathlib.Path(chemin).as_uri())
        log.info(f"[UI] tableau de bord ouvert : {chemin}")
    except Exception as e:                    # jamais bloquer le demarrage
        log.warning(f"[UI] ouverture impossible ({e}) — ouvrir {chemin} a la main")


# ── Main ───────────────────────────────────────────────────────────────────
async def main_async():
    # Auth / OAuth callback server
    auth_srv = await asyncio.start_server(auth_handler, "0.0.0.0", AUTH_PORT)
    log.info(f"[AUTH] Serveur OAuth sur http://localhost:{AUTH_PORT}")

    # WebSocket server
    if HAS_WS:
        # max_size lifted from the 1 MB default so media uploads fit in a frame.
        async with websockets.serve(ws_handler, "localhost", WS_PORT,
                                    max_size=MAX_UPLOAD_BYTES + 2 * 1024 * 1024):
            log.info(f"[WS] Serveur WebSocket sur ws://localhost:{WS_PORT}")
            ouvrir_dashboard()
            async with auth_srv:
                tasks = [scheduler_loop()]
                if HAS_GENIA:
                    log.info("[GENIA] Listener simple actif")
                    tasks.append(genia_listener_loop(
                        get_settings=lambda: SETTINGS,
                        omnipost_state=STATE,
                        save_posts_fn=save_posts,
                        broadcast_fn=broadcast,
                        add_notification_fn=add_notification,
                    ))
                if HAS_PIPELINE:
                    log.info("[PIPELINE] Fabrication → Conversion → Approbation → Drip actif")
                    tasks.append(genia_pipeline.run_all_loops(
                        get_settings=lambda: SETTINGS,
                        omnipost_state=STATE,
                        save_posts_fn=save_posts,
                        publish_post_fn=publish_post,
                        broadcast_fn=broadcast,
                        add_notification_fn=add_notification,
                    ))
                await asyncio.gather(*tasks)
    else:
        async with auth_srv:
            await asyncio.sleep(9999)

def main():
    print("""
╔══════════════════════════════════════════════╗
║      OmniPost v1.1.0 — Démarrage            ║
╠══════════════════════════════════════════════╣
║  Facebook • Instagram • TikTok              ║
║  YouTube • Pinterest • Twitter/X            ║
║  ➜ GeniA listener (cross-post auto)         ║
╚══════════════════════════════════════════════╝
""")
    log.info("[START] OmniPost démarré")
    log.info(f"[WS]   ws://localhost:{WS_PORT}")
    log.info(f"[AUTH] http://localhost:{AUTH_PORT}")
    log.info("[UI]   Ouvre omnipost_dashboard.html")

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log.info("[STOP] OmniPost arrêté")

if __name__ == "__main__":
    main()
