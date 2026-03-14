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
VIDEO_DIR      = "videos"
MUSIC_DIR      = "music"
WS_PORT        = 8860
AUTH_PORT      = 8861

os.makedirs(MEDIA_DIR,   exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR,   exist_ok=True)
os.makedirs(MUSIC_DIR,   exist_ok=True)

# ── Video formats ──────────────────────────────────────────────────────────
VIDEO_FORMATS = {
    "tiktok":    {"w":1080,"h":1920,"fps":30,"max_sec":60,  "label":"TikTok (9:16)"},
    "reels":     {"w":1080,"h":1920,"fps":30,"max_sec":90,  "label":"Instagram Reels (9:16)"},
    "shorts":    {"w":1080,"h":1920,"fps":30,"max_sec":60,  "label":"YouTube Shorts (9:16)"},
    "instagram": {"w":1080,"h":1080,"fps":30,"max_sec":60,  "label":"Instagram Feed (1:1)"},
    "youtube":   {"w":1920,"h":1080,"fps":30,"max_sec":7200,"label":"YouTube (16:9)"},
    "facebook":  {"w":1280,"h":720, "fps":30,"max_sec":240, "label":"Facebook (16:9)"},
    "pinterest": {"w":1000,"h":1500,"fps":30,"max_sec":15,  "label":"Pinterest (2:3)"},
    "twitter":   {"w":1280,"h":720, "fps":30,"max_sec":140, "label":"Twitter/X (16:9)"},
}

def check_ffmpeg() -> bool:
    for cmd in ["ffmpeg","ffmpeg.exe"]:
        try:
            r = subprocess.run([cmd,"-version"],capture_output=True,timeout=5)
            if r.returncode == 0: return True
        except Exception: pass
    return False

HAS_FFMPEG = check_ffmpeg()

async def create_slideshow_video(images:list, output_name:str, platform:str="tiktok",
                                  text_overlay:str="", music_path:str="",
                                  duration_per_image:float=3.0) -> dict:
    if not HAS_FFMPEG:
        return {"status":"error","error":"FFmpeg non installé — télécharge sur https://ffmpeg.org"}
    fmt = VIDEO_FORMATS.get(platform, VIDEO_FORMATS["tiktok"])
    W,H,FPS = fmt["w"],fmt["h"],fmt["fps"]
    output_path = os.path.join(VIDEO_DIR, f"{output_name}_{platform}.mp4")
    ffmpeg = "ffmpeg"
    try:
        input_args = []
        valid_images = []
        for i,img in enumerate(images[:15]):
            if img.startswith("http"):
                local = os.path.join(MEDIA_DIR,f"slide_{i}.jpg")
                try: urllib.request.urlretrieve(img,local); img=local
                except Exception: continue
            if os.path.exists(img):
                input_args.extend(["-loop","1","-t",str(duration_per_image),"-i",img])
                valid_images.append(img)
        if not valid_images:
            return {"status":"error","error":"Aucune image valide trouvée"}
        n = len(valid_images)
        scales = [f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]" for i in range(n)]
        if n > 1:
            concat = "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cv]"
            scales.append(concat)
            prev = "[cv]"
        else:
            prev = "[v0]"
        if text_overlay:
            safe = text_overlay.replace("'","\\'").replace(":","\\:")[:150]
            fs = max(24,min(60,W//20))
            scales.append(f"{prev}drawtext=text='{safe}':fontsize={fs}:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=h-th-60[fv]")
            final = "[fv]"
        else:
            final = prev
        cmd = [ffmpeg,"-y"]+input_args
        if music_path and os.path.exists(music_path):
            cmd.extend(["-i",music_path])
        cmd.extend(["-filter_complex",";".join(scales),"-map",final,
                    "-c:v","libx264","-r",str(FPS),"-pix_fmt","yuv420p"])
        if music_path and os.path.exists(music_path):
            cmd.extend(["-map",f"{n}:a","-c:a","aac","-shortest"])
        cmd.extend(["-t",str(min(n*duration_per_image,fmt["max_sec"])),output_path])
        proc = await asyncio.create_subprocess_exec(*cmd,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        _,stderr = await asyncio.wait_for(proc.communicate(),timeout=120)
        if proc.returncode == 0:
            size = os.path.getsize(output_path)
            return {"status":"success","path":output_path,"platform":platform,
                    "format":fmt["label"],"size_kb":size//1024}
        return {"status":"error","error":stderr.decode(errors="ignore")[-400:]}
    except asyncio.TimeoutError:
        return {"status":"error","error":"Timeout — trop d'images"}
    except Exception as e:
        return {"status":"error","error":str(e)}

async def optimize_video(input_path:str, platform:str) -> dict:
    if not HAS_FFMPEG:
        return {"status":"error","error":"FFmpeg non installé"}
    fmt = VIDEO_FORMATS.get(platform,VIDEO_FORMATS["youtube"])
    name = os.path.splitext(os.path.basename(input_path))[0]
    out  = os.path.join(VIDEO_DIR,f"{name}_{platform}.mp4")
    try:
        cmd = ["ffmpeg","-y","-i",input_path,
               "-vf",f"scale={fmt['w']}:{fmt['h']}:force_original_aspect_ratio=decrease,pad={fmt['w']}:{fmt['h']}:(ow-iw)/2:(oh-ih)/2",
               "-c:v","libx264","-r",str(fmt["fps"]),"-c:a","aac","-b:a","128k",
               "-pix_fmt","yuv420p","-t",str(fmt["max_sec"]),out]
        proc = await asyncio.create_subprocess_exec(*cmd,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        _,stderr = await asyncio.wait_for(proc.communicate(),timeout=300)
        if proc.returncode == 0:
            return {"status":"success","path":out,"format":fmt["label"]}
        return {"status":"error","error":stderr.decode(errors="ignore")[-300:]}
    except Exception as e:
        return {"status":"error","error":str(e)}

async def generate_ai_video_runway(prompt:str, duration:int=4) -> dict:
    api_key = SETTINGS.get("runway_api_key","")
    if not api_key:
        return {"status":"error","error":"Clé API Runway ML manquante — configure dans Paramètres"}
    try:
        data = json.dumps({"text_prompt":prompt,"model":"gen3a_turbo",
                           "duration":duration,"ratio":"1280:768","watermark":False}).encode()
        req = urllib.request.Request(
            "https://api.dev.runwayml.com/v1/image_to_video",data=data,
            headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json",
                     "X-Runway-Version":"2024-11-06"},method="POST")
        with urllib.request.urlopen(req,timeout=30) as r:
            result = json.loads(r.read().decode())
        task_id = result.get("id","")
        if not task_id: return {"status":"error","error":"Pas de task_id"}
        return {"status":"pending","task_id":task_id,"provider":"runway"}
    except Exception as e:
        return {"status":"error","error":f"Runway: {str(e)}"}

async def check_ai_video_status(task_id:str, provider:str="runway") -> dict:
    api_key = SETTINGS.get(f"{provider}_api_key","")
    if not api_key: return {"status":"error","error":f"Clé API {provider} manquante"}
    try:
        req = urllib.request.Request(
            f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
            headers={"Authorization":f"Bearer {api_key}","X-Runway-Version":"2024-11-06"})
        with urllib.request.urlopen(req,timeout=10) as r:
            result = json.loads(r.read().decode())
        status = result.get("status","")
        if status == "SUCCEEDED":
            url = (result.get("output",[]) or [""])[0]
            return {"status":"success","url":url,"provider":provider}
        elif status == "FAILED":
            return {"status":"error","error":result.get("failure","Échec")}
        return {"status":"pending","progress":result.get("progressRatio",0),"task_id":task_id}
    except Exception as e:
        return {"status":"error","error":str(e)}

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
    """Publish a post to all selected platforms — via Buffer or direct API"""
    results = {}

    # Try Buffer first if connected
    buffer_token = SETTINGS.get("buffer_token", "")
    if buffer_token and post.get("use_buffer", True):
        for platform in post.get("platforms", []):
            try:
                result = await _post_via_buffer(buffer_token, platform, post)
                results[platform] = result
                log.info(f"[BUFFER] {platform}: {result.get('status')}")
            except Exception as e:
                # Fallback to direct API
                results[platform] = await _publish_to_platform(platform, post)
    else:
        for platform in post.get("platforms", []):
            try:
                result = await _publish_to_platform(platform, post)
                results[platform] = result
            except Exception as e:
                results[platform] = {"status": "error", "error": str(e)}

    return results

async def _post_via_buffer(token: str, platform: str, post: dict) -> dict:
    """Publish via Buffer API"""
    try:
        # Get Buffer profiles
        req = urllib.request.Request(
            "https://api.bufferapp.com/1/profiles.json",
            headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            profiles = json.loads(r.read().decode())

        # Find matching profile for platform
        service_map = {"facebook": "facebook", "instagram": "instagram",
                       "tiktok": "tiktok", "youtube": "youtube",
                       "pinterest": "pinterest", "twitter": "twitter",
                       "linkedin": "linkedin"}
        service = service_map.get(platform, platform)
        profile = next((p for p in profiles if p.get("service") == service), None)

        if not profile:
            return {"status": "error", "error": f"Aucun profil Buffer pour {platform}"}

        profile_id = profile["id"]
        content    = post.get("content", "")
        hashtags   = " ".join(post.get("hashtags", []))
        full_text  = f"{content}\n\n{hashtags}".strip()
        scheduled  = post.get("scheduled", "")

        data = {
            "profile_ids[]": profile_id,
            "text":          full_text,
        }

        if scheduled:
            try:
                dt = datetime.fromisoformat(scheduled)
                data["scheduled_at"] = dt.isoformat()
                data["now"] = "false"
            except ValueError:
                data["now"] = "true"
        else:
            data["now"] = "true"

        # Add media if present
        media = post.get("media", [])
        if media:
            data["media[photo]"] = media[0]

        payload = urllib.parse.urlencode(data).encode()
        req2 = urllib.request.Request(
            "https://api.bufferapp.com/1/updates/create.json",
            data=payload,
            headers={"Authorization": f"Bearer {token}"},
            method="POST"
        )
        with urllib.request.urlopen(req2, timeout=15) as r:
            result = json.loads(r.read().decode())

        if result.get("success"):
            return {"status": "published" if data.get("now")=="true" else "scheduled",
                    "id": result.get("updates", [{}])[0].get("id", "")}
        else:
            return {"status": "error", "error": result.get("message", "Erreur Buffer")}

    except Exception as e:
        return {"status": "error", "error": f"Buffer: {str(e)}"}

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
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        data = json.dumps({
            "title": description[:100],
            "description": description,
            "link": link or "",
            "media_source": {"source_type": "image_url", "url": media[0] if media else ""}
        }).encode()
        req = urllib.request.Request(
            "https://api.pinterest.com/v5/pins",
            data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read().decode())
        return {"status": "published", "id": result.get("id")}
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

    # ── Buffer ───────────────────────────────────────────────────────────
    elif cmd == "connect_buffer":
        token = msg.get("token", "")
        if not token:
            await ws.send(json.dumps({"type": "buffer_error", "error": "Token manquant"}))
            return
        try:
            req = urllib.request.Request(
                "https://api.bufferapp.com/1/profiles.json",
                headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                profiles = json.loads(r.read().decode())

            connected_platforms = []
            for p in profiles:
                service  = p.get("service", "")
                handle   = p.get("formatted_username", p.get("service_username",""))
                avatar   = p.get("avatar", "")
                STATE.accounts[service] = {
                    "platform":     service,
                    "name":         PLATFORMS.get(service,{}).get("name", service),
                    "handle":       handle,
                    "connected":    True,
                    "via_buffer":   True,
                    "buffer_id":    p.get("id",""),
                    "followers":    p.get("statistics", {}).get("followers", 0),
                    "avatar_url":   avatar,
                }
                connected_platforms.append(service)

            SETTINGS["buffer_token"] = token
            save_settings(SETTINGS)
            log.info(f"[BUFFER] Connecté — {len(connected_platforms)} plateformes: {connected_platforms}")
            add_notification(f"✅ Buffer connecté — {len(connected_platforms)} plateformes", "success")
            await ws.send(json.dumps({
                "type":      "buffer_connected",
                "platforms": connected_platforms,
                "accounts":  STATE.accounts,
                "count":     len(connected_platforms),
            }))
        except Exception as e:
            await ws.send(json.dumps({"type": "buffer_error", "error": str(e)}))

    elif cmd == "disconnect_buffer":
        SETTINGS["buffer_token"] = ""
        save_settings(SETTINGS)
        for p in list(STATE.accounts.keys()):
            if STATE.accounts[p].get("via_buffer"):
                del STATE.accounts[p]
        await ws.send(json.dumps({"type": "buffer_disconnected"}))

    elif cmd == "get_buffer_analytics":
        token = SETTINGS.get("buffer_token", "")
        if not token:
            await ws.send(json.dumps({"type": "analytics_error", "error": "Buffer non connecté"}))
            return
        try:
            analytics = []
            for platform, acc in STATE.accounts.items():
                if not acc.get("via_buffer"):
                    continue
                profile_id = acc.get("buffer_id", "")
                if not profile_id:
                    continue
                req = urllib.request.Request(
                    f"https://api.bufferapp.com/1/profiles/{profile_id}/updates/sent.json?count=50",
                    headers={"Authorization": f"Bearer {token}"}
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    updates = json.loads(r.read().decode())
                for u in updates.get("updates", []):
                    stats = u.get("statistics", {})
                    analytics.append({
                        "platform":    platform,
                        "date":        datetime.fromtimestamp(u.get("created_at",0)).strftime("%Y-%m-%d"),
                        "impressions": stats.get("reach", 0),
                        "likes":       stats.get("likes", stats.get("favorites", 0)),
                        "comments":    stats.get("comments", 0),
                        "shares":      stats.get("shares", stats.get("retweets", 0)),
                        "clicks":      stats.get("clicks", 0),
                    })
            STATE.analytics = analytics
            save_analytics()
            await ws.send(json.dumps({"type": "analytics_data", "data": analytics}))
            add_notification(f"📊 Analytics Buffer chargés — {len(analytics)} entrées", "info")
        except Exception as e:
            await ws.send(json.dumps({"type": "analytics_error", "error": str(e)}))

    # ── Vidéo ────────────────────────────────────────────────────────────
    elif cmd == "create_video":
        images   = msg.get("images", [])
        platform = msg.get("platform", "tiktok")
        text     = msg.get("text", "")
        music    = msg.get("music", "")
        duration = float(msg.get("duration_per_image", 3.0))
        name     = msg.get("name", f"video_{int(time.time())}")

        await ws.send(json.dumps({"type":"video_progress","message":f"Création vidéo {platform}…","progress":10}))
        result = await create_slideshow_video(images, name, platform, text, music, duration)
        await ws.send(json.dumps({"type":"video_result","result":result}))
        if result["status"] == "success":
            add_notification(f"🎬 Vidéo créée: {result.get('format')} ({result.get('size_kb')}KB)", "success")

    elif cmd == "optimize_video":
        input_path = msg.get("path","")
        platforms  = msg.get("platforms", ["tiktok","reels","shorts"])
        results = {}
        for p in platforms:
            await ws.send(json.dumps({"type":"video_progress","message":f"Optimisation pour {p}…","progress":20}))
            r = await optimize_video(input_path, p)
            results[p] = r
        await ws.send(json.dumps({"type":"video_optimized","results":results}))
        add_notification(f"✅ Vidéo optimisée pour {len(platforms)} plateformes", "success")

    elif cmd == "generate_ai_video":
        prompt   = msg.get("prompt","")
        provider = msg.get("provider","runway")
        duration = msg.get("duration", 4)
        await ws.send(json.dumps({"type":"video_progress","message":f"Envoi à {provider}…","progress":5}))
        if provider == "runway":
            result = await generate_ai_video_runway(prompt, duration)
        else:
            result = {"status":"error","error":f"Provider {provider} non supporté"}
        await ws.send(json.dumps({"type":"ai_video_started","result":result}))

    elif cmd == "check_ai_video":
        task_id  = msg.get("task_id","")
        provider = msg.get("provider","runway")
        result   = await check_ai_video_status(task_id, provider)
        await ws.send(json.dumps({"type":"ai_video_status","result":result}))

    elif cmd == "get_video_formats":
        await ws.send(json.dumps({"type":"video_formats","formats":VIDEO_FORMATS,"ffmpeg":HAS_FFMPEG}))

    elif cmd == "list_videos":
        videos = []
        if os.path.exists(VIDEO_DIR):
            for f in os.listdir(VIDEO_DIR):
                if f.endswith(".mp4"):
                    path = os.path.join(VIDEO_DIR,f)
                    videos.append({"name":f,"path":path,"size_kb":os.path.getsize(path)//1024,
                                   "created":datetime.fromtimestamp(os.path.getctime(path)).strftime("%Y-%m-%d %H:%M")})
        await ws.send(json.dumps({"type":"video_list","videos":sorted(videos,key=lambda x:-os.path.getctime(x["path"]) if os.path.exists(x["path"]) else 0)}))

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
                await asyncio.gather(
                    scheduler_loop(),
                )
    else:
        async with auth_srv:
            await asyncio.sleep(9999)

def main():
    print("""
╔══════════════════════════════════════════════╗
║        OmniPost v1.1.0 — Démarrage          ║
╠══════════════════════════════════════════════╣
║  Facebook • Instagram • TikTok              ║
║  YouTube • Pinterest • Twitter/X            ║
╚══════════════════════════════════════════════╝
""")
    log.info("[START] OmniPost démarré")
    log.info(f"[WS]   ws://localhost:{WS_PORT}")
    log.info(f"[AUTH] http://localhost:{AUTH_PORT}")
    log.info("[UI]   Ouverture du dashboard…")

    # Ouvrir le dashboard automatiquement
    import webbrowser, sys
    # PyInstaller: fichiers dans sys._MEIPASS, sinon dossier courant
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    dashboard = os.path.join(base_dir, "omnipost_dashboard.html")
    if not os.path.exists(dashboard):
        dashboard = os.path.join(os.path.dirname(sys.executable), "omnipost_dashboard.html")
    if os.path.exists(dashboard):
        # Petit délai pour que le serveur démarre avant le navigateur
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(f"file:///{dashboard.replace(os.sep, '/')}")
            log.info("[UI] Dashboard ouvert dans le navigateur")
        threading.Thread(target=open_browser, daemon=True).start()
    else:
        log.warning(f"[UI] Dashboard non trouvé: {dashboard}")

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log.info("[STOP] OmniPost arrêté")

if __name__ == "__main__":
    main()
