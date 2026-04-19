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
import base64
import urllib.request
import urllib.parse
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
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
        return DEFAULT_SETTINGS.copy()
    with open(SETTINGS_FILE, "r") as f:
        s = json.load(f)
    # Merge with defaults for new keys
    for k, v in DEFAULT_SETTINGS.items():
        if k not in s:
            s[k] = v
    return s

def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w") as f:
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

# ── Platform connectors ────────────────────────────────────────────────────
def get_oauth_url(platform: str) -> str:
    """Returns OAuth authorization URL for a platform"""
    cfg = SETTINGS.get("oauth", {}).get(platform, {})
    callback = f"http://localhost:{AUTH_PORT}/oauth/callback/{platform}"

    if platform in ("facebook", "instagram"):
        app_id = cfg.get("app_id", "")
        if not app_id:
            return ""
        scope = "pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish"
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
        return (f"https://accounts.google.com/o/oauth2/v2/auth"
                f"?client_id={client_id}&redirect_uri={urllib.parse.quote(callback)}"
                f"&response_type=code&scope={urllib.parse.quote(scope)}&access_type=offline")

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
            log.info(f"[PUBLISH] {platform}: {result.get('status')}")
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
    content = post.get("content", "")
    hashtags = " ".join(post.get("hashtags", []))
    full_text = f"{content}\n\n{hashtags}".strip()
    media = post.get("media", [])

    if platform == "facebook":
        return await _post_facebook(token, full_text, media)
    elif platform == "instagram":
        return await _post_instagram(token, full_text, media)
    elif platform == "tiktok":
        return await _post_tiktok(token, full_text, media)
    elif platform == "youtube":
        return await _post_youtube(token, post.get("title", content[:100]), full_text, media)
    elif platform == "pinterest":
        return await _post_pinterest(token, full_text, media, post.get("link", ""))
    elif platform == "twitter":
        return await _post_twitter(post, full_text, media)
    return {"status": "error", "error": "Unknown platform"}

async def _post_facebook(token: str, text: str, media: list) -> dict:
    try:
        # Get page ID first
        req = urllib.request.Request(
            f"https://graph.facebook.com/v18.0/me/accounts?access_token={token}"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            pages = json.loads(r.read().decode())
        if not pages.get("data"):
            return {"status": "error", "error": "No pages found"}
        page = pages["data"][0]
        page_token = page["access_token"]
        page_id    = page["id"]

        data = {"message": text, "access_token": page_token}
        payload = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            f"https://graph.facebook.com/v18.0/{page_id}/feed",
            data=payload, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read().decode())
        return {"status": "published", "id": result.get("id")}
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def _post_instagram(token: str, caption: str, media: list) -> dict:
    try:
        # Instagram requires a media upload first
        if not media:
            return {"status": "error", "error": "Instagram requires at least one image/video"}
        # Get IG business account
        req = urllib.request.Request(
            f"https://graph.facebook.com/v18.0/me?fields=instagram_business_account&access_token={token}"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        ig_id = data.get("instagram_business_account", {}).get("id")
        if not ig_id:
            return {"status": "error", "error": "No Instagram Business account linked"}
        # Step 1: Create media container
        media_url = media[0] if media else ""
        payload = urllib.parse.urlencode({
            "image_url": media_url, "caption": caption, "access_token": token
        }).encode()
        req = urllib.request.Request(
            f"https://graph.facebook.com/v18.0/{ig_id}/media",
            data=payload, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            container = json.loads(r.read().decode())
        container_id = container.get("id")
        # Step 2: Publish
        payload2 = urllib.parse.urlencode({
            "creation_id": container_id, "access_token": token
        }).encode()
        req2 = urllib.request.Request(
            f"https://graph.facebook.com/v18.0/{ig_id}/media_publish",
            data=payload2, method="POST"
        )
        with urllib.request.urlopen(req2, timeout=15) as r:
            result = json.loads(r.read().decode())
        return {"status": "published", "id": result.get("id")}
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def _post_tiktok(token: str, text: str, media: list) -> dict:
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        # TikTok video upload is multi-step
        # For now return instructions
        return {"status": "manual", "message": "TikTok requires video upload — use TikTok Creator Portal"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def _post_youtube(token: str, title: str, description: str, media: list) -> dict:
    try:
        if not media:
            return {"status": "error", "error": "YouTube requires a video file"}
        return {"status": "manual", "message": f"Upload video to YouTube Studio with title: {title}"}
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

async def _post_twitter(post: dict, text: str, media: list) -> dict:
    try:
        cfg = SETTINGS.get("oauth", {}).get("twitter", {})
        bearer = cfg.get("bearer_token", "")
        if not bearer:
            return {"status": "error", "error": "Twitter Bearer Token not configured"}
        headers = {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
        data = json.dumps({"text": text[:280]}).encode()
        req = urllib.request.Request(
            "https://api.twitter.com/2/tweets",
            data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode())
        return {"status": "published", "id": result.get("data", {}).get("id")}
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

async def handle_command(ws, msg: dict):
    cmd = msg.get("cmd")

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
            SETTINGS["oauth"][platform] = keys
            save_settings(SETTINGS)
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

            if code:
                log.info(f"[OAUTH] Code reçu pour {platform}")
                # Store code for token exchange
                if platform not in STATE.accounts:
                    STATE.accounts[platform] = {}
                STATE.accounts[platform]["oauth_code"]  = code
                STATE.accounts[platform]["connected"]   = True
                STATE.accounts[platform]["platform"]    = platform
                STATE.accounts[platform]["name"]        = PLATFORMS.get(platform, {}).get("name", platform)
                # Notify dashboard
                asyncio.create_task(broadcast({
                    "type":     "platform_connected",
                    "platform": platform,
                    "account":  STATE.accounts[platform],
                }))
                add_notification(f"✅ {PLATFORMS.get(platform,{}).get('name',platform)} connecté !", "success")
                body = f"""<html><body style="background:#0f0f13;color:#3dffb4;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
                    <div style="text-align:center"><h2>✅ {platform.title()} connecté !</h2>
                    <p style="color:#9090a8">Tu peux fermer cette fenêtre.</p>
                    <script>window.close();</script></div></body></html>"""
            else:
                body = f"""<html><body style="background:#0f0f13;color:#ff4d6a;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
                    <div style="text-align:center"><h2>❌ Erreur: {error}</h2>
                    <script>window.close();</script></div></body></html>"""

            writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n{cors}\r\n{body}".encode())

        else:
            body = json.dumps({"status": "OmniPost Auth Server", "version": "1.0.0"})
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n{cors}\r\n{body}".encode())

        await writer.drain()
    except Exception as e:
        log.debug(f"[AUTH] Erreur: {e}")
    finally:
        writer.close()

# ── Main ───────────────────────────────────────────────────────────────────
async def main_async():
    # Auth / OAuth callback server
    auth_srv = await asyncio.start_server(auth_handler, "0.0.0.0", AUTH_PORT)
    log.info(f"[AUTH] Serveur OAuth sur http://localhost:{AUTH_PORT}")

    # WebSocket server
    if HAS_WS:
        async with websockets.serve(ws_handler, "localhost", WS_PORT):
            log.info(f"[WS] Serveur WebSocket sur ws://localhost:{WS_PORT}")
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
