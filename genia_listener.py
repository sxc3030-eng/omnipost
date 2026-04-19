"""
OmniPost — GeniA Listener Add-on
Polls the GIa Underground API for new feed posts and turns them into
OmniPost drafts/scheduled posts, ready to cross-post.

Behavior
--------
- Polls https://api.genia.social/rest/posts every POLL_SECONDS
- Tracks last_seen post id in genia_listener_state.json
- For each new post: downloads media, generates platform-specific captions,
  appends to STATE.posts as status='draft' (or 'scheduled' if auto-publish on)
- Skips posts older than LOOKBACK_HOURS on first run

Settings (added to omnipost_settings.json under "genia"):
{
  "enabled": true,
  "api_url": "https://api.genia.social",
  "api_token": "",                  // optional service key
  "auto_publish": false,            // false = draft for review
  "platforms": ["instagram", "tiktok", "facebook"],
  "default_hashtags": ["#metal", "#underground", "#GIaUnderground"],
  "poll_seconds": 300,
  "lookback_hours": 24,
  "credit_text": "via GIa Underground 🤘 https://genia.social"
}
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger("omnipost.genia")

STATE_FILE = "genia_listener_state.json"
MEDIA_DIR  = "media"

DEFAULT_GENIA = {
    "enabled": False,
    "api_url": "https://api.genia.social",
    "api_token": "",
    "auto_publish": False,
    "platforms": ["instagram", "facebook", "tiktok"],
    "default_hashtags": ["#metal", "#underground", "#GIaUnderground"],
    "poll_seconds": 300,
    "lookback_hours": 24,
    "credit_text": "via GIa Underground 🤘 https://genia.social",
}


# ── Persistence ────────────────────────────────────────────────────────

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"last_seen_id": None, "last_seen_at": None, "imported_ids": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_seen_id": None, "last_seen_at": None, "imported_ids": []}


def save_state(state: dict):
    # Cap imported_ids list to last 1000 to prevent unbounded growth
    state["imported_ids"] = state.get("imported_ids", [])[-1000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── HTTP ───────────────────────────────────────────────────────────────

def _http_json(url: str, token: str = "", timeout: int = 15):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "OmniPost-GeniA-Listener/1.0",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url: str, dest: str, timeout: int = 30) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OmniPost/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        log.warning(f"[genia] download failed {url}: {e}")
        return False


# ── Caption builder ────────────────────────────────────────────────────

def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_caption(post: dict, cfg: dict, platform: str) -> str:
    """Per-platform caption with credit + hashtags."""
    artist  = (post.get("artist_name") or "").strip()
    caption = (post.get("caption") or "").strip()
    link    = (post.get("audio_url") or post.get("video_url") or "").strip()

    header = f"🤘 {artist}\n\n" if artist else ""
    body   = caption
    credit = "\n\n" + cfg.get("credit_text", "")

    hashtags = " ".join(cfg.get("default_hashtags", []))
    tail = f"\n\n{hashtags}" if hashtags else ""

    full = f"{header}{body}{credit}{tail}".strip()

    # Per-platform truncation
    limits = {
        "twitter": 270,
        "pinterest": 480,
        "instagram": 2100,
        "tiktok": 2100,
        "facebook": 5000,
        "youtube": 4900,
        "linkedin": 2900,
    }
    return _truncate(full, limits.get(platform, 2100))


# ── Import loop ────────────────────────────────────────────────────────

async def _import_post(genia_post: dict, cfg: dict, omnipost_state, save_posts_fn,
                        broadcast_fn, add_notification_fn) -> bool:
    pid = genia_post.get("id")
    if not pid:
        return False

    # Pick best media URL
    media_url = (
        genia_post.get("video_url")
        or genia_post.get("image_url")
        or genia_post.get("media_url")
    )

    media_files = []
    if media_url:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        ext = ".mp4" if media_url.lower().endswith((".mp4", ".mov", ".webm")) else ".jpg"
        local = os.path.join(MEDIA_DIR, f"genia_{pid}{ext}")
        if _download(media_url, local):
            media_files.append(local)

    platforms = list(cfg.get("platforms", []))
    if not platforms:
        log.warning("[genia] no target platforms configured, skipping")
        return False

    # First platform's caption is the canonical one
    primary_caption = build_caption(genia_post, cfg, platforms[0])

    op_post = {
        "id":           f"genia_{pid}",
        "platforms":    platforms,
        "content":      primary_caption,
        "media":        media_files,
        "hashtags":     list(cfg.get("default_hashtags", [])),
        "scheduled":    "",
        "status":       "scheduled" if cfg.get("auto_publish") else "draft",
        "created_at":   datetime.now().isoformat(),
        "published_at": "",
        "results":      {},
        "title":        (genia_post.get("artist_name") or "")[:100] or "GIa Underground",
        "tags":         [],
        "link":         f"https://genia.social/post/{pid}",
        "source":       "genia",
        "source_id":    pid,
    }

    if cfg.get("auto_publish"):
        # Publish in 2 minutes — leaves room for cancel
        op_post["scheduled"] = (datetime.now() + timedelta(minutes=2)).isoformat()

    omnipost_state.posts.append(op_post)
    save_posts_fn()
    await broadcast_fn({"type": "post_created", "post": op_post})
    add_notification_fn(
        f"📥 GeniA: nouveau post «{op_post['title']}» {'programmé' if cfg.get('auto_publish') else 'en draft'}",
        "info",
    )
    log.info(f"[genia] imported {pid} -> omnipost {op_post['id']}")
    return True


async def _poll_once(cfg: dict, omnipost_state, save_posts_fn,
                     broadcast_fn, add_notification_fn):
    state = load_state()
    imported_ids = set(state.get("imported_ids", []))

    api_url = cfg["api_url"].rstrip("/")
    token   = cfg.get("api_token", "")

    # Build query — newest first, capped
    params = {
        "select": "id,caption,artist_name,image_url,audio_url,video_url,created_at",
        "order":  "created_at.desc",
        "limit":  "20",
    }

    # On first run, only look at lookback window
    if not state.get("last_seen_id"):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.get("lookback_hours", 24))
        params["created_at"] = f"gte.{cutoff.isoformat()}"

    url = f"{api_url}/rest/posts?{urllib.parse.urlencode(params)}"

    try:
        rows = _http_json(url, token=token)
    except Exception as e:
        log.error(f"[genia] poll failed: {e}")
        return

    if not isinstance(rows, list):
        log.warning(f"[genia] unexpected response: {type(rows)}")
        return

    # Process oldest-first so chronological order is preserved
    new = [r for r in reversed(rows) if r.get("id") and r["id"] not in imported_ids]

    if not new:
        return

    log.info(f"[genia] {len(new)} new posts to import")

    for row in new:
        ok = await _import_post(
            row, cfg, omnipost_state, save_posts_fn, broadcast_fn, add_notification_fn,
        )
        if ok:
            imported_ids.add(row["id"])
            state["last_seen_id"] = row["id"]
            state["last_seen_at"] = row.get("created_at")

    state["imported_ids"] = sorted(imported_ids)
    save_state(state)


async def listener_loop(get_settings, omnipost_state, save_posts_fn,
                        broadcast_fn, add_notification_fn):
    """Run forever. get_settings() returns the live SETTINGS dict."""
    log.info("[genia] listener loop started")
    while True:
        try:
            settings = get_settings() or {}
            cfg = {**DEFAULT_GENIA, **(settings.get("genia") or {})}
            if cfg.get("enabled"):
                await _poll_once(cfg, omnipost_state, save_posts_fn,
                                 broadcast_fn, add_notification_fn)
        except Exception as e:
            log.error(f"[genia] loop error: {e}")
        # Sleep with current poll_seconds (re-read each iter so changes take effect)
        delay = max(60, int(cfg.get("poll_seconds", 300)) if 'cfg' in dir() else 300)
        await asyncio.sleep(delay)
