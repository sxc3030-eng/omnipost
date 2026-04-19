"""
OmniPost — GeniA Pipeline (Fabrication → Conversion → Approbation → Publication)

Workflow:
  1. INGEST     — poll GIa Underground API, drop new posts in pipeline/created/
  2. CONVERT    — ffmpeg builds 9:16 TikTok-ready MP4, moves to pipeline/converted/
  3. APPROVE    — user reviews via WS dashboard, batch approve → pipeline/approved/
  4. DRIP       — release N posts/day at configured time → pipeline/published/

Each post = one folder:
  pipeline/<status>/<post_id>/
    meta.json          # source data + state + scheduled_at + caption per platform
    cover.jpg          # source image
    audio.mp3          # spotify preview (optional)
    tiktok.mp4         # converted vertical video (after conversion)

WS commands (handled by omnipost.py):
  pipeline_status              → counts per phase
  pipeline_list <phase>        → list folders + meta
  pipeline_approve <ids|all>   → move to approved/
  pipeline_reject <ids>        → delete folder
  pipeline_save_config <cfg>   → update genia settings
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("omnipost.pipeline")

# ── Config ─────────────────────────────────────────────────────────────

PIPELINE_ROOT = Path(os.environ.get("GENIA_PIPELINE_ROOT", "pipeline"))
PHASES = ["created", "converted", "approved", "published", "failed"]

DEFAULT_GENIA = {
    "enabled":          False,
    "api_url":          "https://api.genia.social",
    "api_token":        "",
    "platforms":        ["instagram", "facebook", "tiktok"],
    "default_hashtags": ["#metal", "#underground", "#GIaUnderground"],
    "credit_text":      "via GIa Underground 🤘 https://genia.social",
    # Ingest
    "poll_seconds":     300,         # check API every 5 min
    "lookback_hours":   24,
    # Convert
    "convert_seconds":  120,         # ffmpeg pass every 2 min
    "video_duration":   30,          # TikTok sweet spot
    "video_width":      1080,
    "video_height":     1920,
    "font_path":        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    # Drip
    "drip_per_day":     1,
    "drip_hour":        14,          # 14:00 local
    "drip_seconds":     600,         # check every 10 min
    "auto_publish":     True,        # if false, drip just stages without publishing
}

INGEST_STATE_FILE = "pipeline_ingest_state.json"
DRIP_STATE_FILE   = "pipeline_drip_state.json"

# Platform caption length limits
LIMITS = {
    "twitter": 270, "pinterest": 480, "instagram": 2100, "tiktok": 2100,
    "facebook": 5000, "youtube": 4900, "linkedin": 2900,
}


# ── Filesystem helpers ─────────────────────────────────────────────────

def ensure_dirs():
    for p in PHASES:
        (PIPELINE_ROOT / p).mkdir(parents=True, exist_ok=True)


def post_dir(phase: str, post_id: str) -> Path:
    return PIPELINE_ROOT / phase / post_id


def find_post(post_id: str):
    """Return (phase, path) for a post_id, or (None, None)."""
    for phase in PHASES:
        p = post_dir(phase, post_id)
        if p.exists():
            return phase, p
    return None, None


def move_post(post_id: str, to_phase: str) -> Path | None:
    phase, src = find_post(post_id)
    if not src:
        return None
    dest = post_dir(to_phase, post_id)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))
    return dest


def read_meta(folder: Path) -> dict:
    f = folder / "meta.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_meta(folder: Path, meta: dict):
    (folder / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def list_phase(phase: str) -> list:
    """Return list of meta dicts for a phase, newest first."""
    base = PIPELINE_ROOT / phase
    if not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir():
            m = read_meta(d)
            if m:
                m["_folder"] = str(d)
                out.append(m)
    return out


def phase_counts() -> dict:
    return {p: len([d for d in (PIPELINE_ROOT / p).iterdir() if d.is_dir()])
            if (PIPELINE_ROOT / p).exists() else 0 for p in PHASES}


# ── HTTP ───────────────────────────────────────────────────────────────

def _http_json(url: str, token: str = "", timeout: int = 15):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "OmniPost-GeniA-Pipeline/1.0",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _download(url: str, dest: Path, timeout: int = 30) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OmniPost/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        dest.write_bytes(data)
        return True
    except Exception as e:
        log.warning(f"[pipeline] download failed {url}: {e}")
        return False


# ── Caption builder ────────────────────────────────────────────────────

def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build_captions(post: dict, cfg: dict) -> dict:
    """Return {platform: caption} for all configured platforms."""
    artist  = (post.get("caption") or "").strip().split(chr(10))[0][:60].strip()
    caption = (post.get("caption") or "").strip()
    pid     = post.get("id", "")

    header = f"🤘 {artist}\n\n" if artist else ""
    body   = caption
    link   = f"\n\n👉 {cfg.get('credit_text', '')}"
    if pid:
        link += f"\nhttps://genia.social/post/{pid}"

    hashtags = " ".join(cfg.get("default_hashtags", []))
    tail = f"\n\n{hashtags}" if hashtags else ""

    full = f"{header}{body}{link}{tail}".strip()

    return {p: _truncate(full, LIMITS.get(p, 2100)) for p in cfg.get("platforms", [])}


# ── PHASE 1 — INGEST ───────────────────────────────────────────────────

def _load_ingest_state() -> dict:
    if not os.path.exists(INGEST_STATE_FILE):
        return {"imported_ids": []}
    try:
        return json.loads(open(INGEST_STATE_FILE).read())
    except Exception:
        return {"imported_ids": []}


def _save_ingest_state(state: dict):
    state["imported_ids"] = state.get("imported_ids", [])[-2000:]
    open(INGEST_STATE_FILE, "w").write(json.dumps(state, indent=2))


async def _ingest_one(post: dict, cfg: dict, broadcast_fn, add_notification_fn):
    pid = post.get("id")
    if not pid:
        return False

    folder = post_dir("created", pid)
    folder.mkdir(parents=True, exist_ok=True)

    # Determine media type from real schema (media_url + type/thumbnail_url)
    media_url = post.get("media_url") or ""
    thumb_url = post.get("thumbnail_url") or ""
    is_video = (
        post.get("type") in ("video", "clip")
        or media_url.lower().endswith((".mp4", ".mov", ".webm"))
        or "youtube.com" in media_url
        or "youtu.be" in media_url
    )

    # YouTube links: use the thumbnail as cover (we don't redistribute YT video)
    if "youtube.com" in media_url or "youtu.be" in media_url:
        if thumb_url:
            _download(thumb_url, folder / "cover.jpg")
        is_video = False  # treat as image+text card
    elif is_video:
        _download(media_url, folder / "source.mp4")
        if thumb_url:
            _download(thumb_url, folder / "cover.jpg")
    else:
        # Image post
        if media_url:
            _download(media_url, folder / "cover.jpg")
        elif thumb_url:
            _download(thumb_url, folder / "cover.jpg")

    # Audio preview if present
    aud_url = post.get("audio_url")
    if aud_url:
        _download(aud_url, folder / "audio.mp3")

    meta = {
        "id":           pid,
        "status":       "created",
        "created_at":   datetime.now().isoformat(),
        "source":       "genia",
        "source_post":  post,
        "captions":     build_captions(post, cfg),
        "platforms":    list(cfg.get("platforms", [])),
        "link":         f"https://genia.social/post/{pid}",
        "title":        ((post.get("caption") or "").split(chr(10))[0][:100] or "GIa Underground"),
        "has_image":    (folder / "cover.jpg").exists(),
        "has_audio":    (folder / "audio.mp3").exists(),
        "has_source_video": (folder / "source.mp4").exists(),
    }
    write_meta(folder, meta)

    await broadcast_fn({"type": "pipeline_post_created", "id": pid, "meta": meta})
    add_notification_fn(f"📥 GeniA: «{meta['title']}» ajouté à la fabrication", "info")
    log.info(f"[ingest] {pid} -> created/")
    return True


async def _ingest_loop(get_settings, broadcast_fn, add_notification_fn):
    log.info("[pipeline] ingest loop started")
    while True:
        delay = 300
        try:
            cfg = {**DEFAULT_GENIA, **((get_settings() or {}).get("genia") or {})}
            delay = max(60, int(cfg.get("poll_seconds", 300)))
            if not cfg.get("enabled"):
                await asyncio.sleep(delay)
                continue

            state = _load_ingest_state()
            seen = set(state.get("imported_ids", []))

            params = {
                "select": "id,type,caption,media_url,thumbnail_url,audio_url,created_at",
                "order":  "created_at.desc",
                "limit":  "30",
            }
            if not seen:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.get("lookback_hours", 24))
                params["created_at"] = f"gte.{cutoff.isoformat()}"

            url = f"{cfg['api_url'].rstrip('/')}/rest/posts?{urllib.parse.urlencode(params)}"
            try:
                rows = _http_json(url, token=cfg.get("api_token", ""))
            except Exception as e:
                log.error(f"[ingest] API poll failed: {e}")
                await asyncio.sleep(delay)
                continue

            if not isinstance(rows, list):
                await asyncio.sleep(delay)
                continue

            new = [r for r in reversed(rows) if r.get("id") and r["id"] not in seen]
            if new:
                log.info(f"[ingest] {len(new)} new posts to fabricate")
                for r in new:
                    if await _ingest_one(r, cfg, broadcast_fn, add_notification_fn):
                        seen.add(r["id"])
                state["imported_ids"] = sorted(seen)
                _save_ingest_state(state)
        except Exception as e:
            log.error(f"[ingest] loop error: {e}")
        await asyncio.sleep(delay)


# ── PHASE 2 — CONVERT (ffmpeg → 9:16 vertical) ─────────────────────────

def _sanitize_text(s: str, maxlen: int = 80) -> str:
    if not s:
        return ""
    s = re.sub(r"[\r\n]+", " ", str(s))
    s = s.replace("\\", "").replace(":", "\\:").replace("'", "")
    return s.strip()[:maxlen]


def _wrap_text(s: str, max_chars_per_line: int = 22, max_lines: int = 2) -> str:
    """Wrap text on word boundaries. Returns ffmpeg drawtext-compatible string with literal newlines."""
    if not s:
        return ""
    words = s.split()
    lines = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars_per_line:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w[:max_chars_per_line]
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    # Add ellipsis if truncated
    if len(" ".join(lines)) < len(s):
        if lines:
            lines[-1] = lines[-1][:-1] + "..." if len(lines[-1]) > max_chars_per_line - 3 else lines[-1] + "..."
    # ffmpeg drawtext: literal newline = chr(10)
    return chr(10).join(lines)


def _build_video(meta: dict, folder: Path, cfg: dict) -> bool:
    """ffmpeg compose vertical video. Returns True on success."""
    img = folder / "cover.jpg"
    aud = folder / "audio.mp3"
    src_video = folder / "source.mp4"
    out = folder / "tiktok.mp4"

    width  = cfg.get("video_width", 1080)
    height = cfg.get("video_height", 1920)
    dur    = cfg.get("video_duration", 30)
    fps    = 30
    font   = cfg.get("font_path", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

    # If the source is already a video, just letterbox/scale to 9:16
    if src_video.exists():
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
        cmd = [
            "ffmpeg", "-y", "-i", str(src_video),
            "-vf", vf, "-t", str(dur),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-shortest",
            str(out),
        ]
    else:
        if not img.exists():
            log.warning(f"[convert] {meta.get('id')}: no cover image and no source video")
            return False

        # Keep full image visible, letterbox to 9:16 with safe text bands at top/bottom.
        # The image fits inside ~70% of the height (h * 0.70 = ~1344px for 1920),
        # leaving ~288px top + ~288px bottom for text overlays on dark bg.
        img_h = int(height * 0.70)
        # Scale image preserving aspect ratio so longest side fits the safe area
        # then pad center to 1080x1920 with black bg
        kenburns = (
            f"scale='min(iw*{img_h}/ih\,{width})':'min({img_h}\,ih*{width}/iw)':"
            f"force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps}"
        )
        # Title (artist) at top, wrapped to 2 lines, max ~22 chars/line at fontsize 56
        artist_raw = meta.get("title") or ""
        artist_wrapped = _wrap_text(artist_raw, max_chars_per_line=22, max_lines=2)
        artist_t = _sanitize_text(artist_wrapped, maxlen=120)

        # Caption (album/track info) at bottom, wrapped to 2 lines max
        caption_raw = (meta.get("source_post") or {}).get("caption") or ""
        caption_first_chunk = caption_raw.strip().split(chr(10))[0]
        caption_wrapped = _wrap_text(caption_first_chunk, max_chars_per_line=28, max_lines=2)
        title_t = _sanitize_text(caption_wrapped, maxlen=140)

        # All texts use line_spacing for multi-line, semi-transparent black box for legibility,
        # safe margins (90px from edges) so nothing gets cut off on TikTok/Reels UI overlays
        drawtext = (
            f",drawtext=fontfile={font}:text='{artist_t}':"
            f"fontsize=56:fontcolor=white:bordercolor=black:borderw=4:"
            f"line_spacing=12:"
            f"x=(w-text_w)/2:y=80"
            f",drawtext=fontfile={font}:text='{title_t}':"
            f"fontsize=36:fontcolor=#DC2626:bordercolor=black:borderw=3:"
            f"line_spacing=10:"
            f"x=(w-text_w)/2:y=h-300"
            f",drawtext=fontfile={font}:text='GIa Underground':"
            f"fontsize=26:fontcolor=white@0.75:"
            f"x=(w-text_w)/2:y=h-180"
        )
        vf = kenburns + drawtext

        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(img)]
        if aud.exists():
            cmd += ["-i", str(aud)]
        cmd += [
            "-vf", vf, "-t", str(dur), "-r", str(fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "fast", "-crf", "23",
        ]
        if aud.exists():
            cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
        else:
            cmd += ["-an"]
        cmd += [str(out)]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            log.error(f"[ffmpeg] FAIL {meta.get('id')}: {r.stderr[-400:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        log.error(f"[ffmpeg] timeout {meta.get('id')}")
        return False


async def _convert_loop(get_settings, broadcast_fn, add_notification_fn):
    log.info("[pipeline] convert loop started")
    while True:
        delay = 120
        try:
            cfg = {**DEFAULT_GENIA, **((get_settings() or {}).get("genia") or {})}
            delay = max(30, int(cfg.get("convert_seconds", 120)))

            for meta in list_phase("created"):
                pid = meta["id"]
                folder = Path(meta["_folder"])

                # Run blocking ffmpeg in thread executor to not block event loop
                loop = asyncio.get_event_loop()
                ok = await loop.run_in_executor(None, _build_video, meta, folder, cfg)

                if ok:
                    meta["status"] = "converted"
                    meta["converted_at"] = datetime.now().isoformat()
                    meta["video_path"] = str(folder / "tiktok.mp4")
                    write_meta(folder, meta)
                    new_folder = move_post(pid, "converted")
                    if new_folder:
                        meta["_folder"] = str(new_folder)
                    await broadcast_fn({"type": "pipeline_post_converted", "id": pid, "meta": meta})
                    add_notification_fn(f"🎬 «{meta.get('title')}» converti en vidéo 9:16", "success")
                    log.info(f"[convert] {pid} -> converted/")
                else:
                    meta["status"] = "failed"
                    meta["error"] = "ffmpeg conversion failed"
                    write_meta(folder, meta)
                    move_post(pid, "failed")
                    await broadcast_fn({"type": "pipeline_post_failed", "id": pid, "meta": meta})
        except Exception as e:
            log.error(f"[convert] loop error: {e}")
        await asyncio.sleep(delay)


# ── PHASE 3 — APPROVAL (called from WS handler) ────────────────────────

async def approve(post_ids, broadcast_fn, add_notification_fn) -> dict:
    """Move converted/<id> → approved/<id>. post_ids = list or 'all'."""
    if post_ids == "all":
        ids = [m["id"] for m in list_phase("converted")]
    else:
        ids = list(post_ids or [])

    moved = []
    for pid in ids:
        phase, src = find_post(pid)
        if phase != "converted":
            continue
        meta = read_meta(src)
        meta["status"] = "approved"
        meta["approved_at"] = datetime.now().isoformat()
        write_meta(src, meta)
        move_post(pid, "approved")
        moved.append(pid)

    if moved:
        await broadcast_fn({"type": "pipeline_approved", "ids": moved, "count": len(moved)})
        add_notification_fn(f"✅ {len(moved)} post(s) approuvé(s) pour publication", "success")
    return {"approved": moved, "count": len(moved)}


async def reject(post_ids, broadcast_fn, add_notification_fn) -> dict:
    """Permanently delete the post folder(s)."""
    ids = list(post_ids or [])
    deleted = []
    for pid in ids:
        phase, src = find_post(pid)
        if not src:
            continue
        try:
            shutil.rmtree(src)
            deleted.append(pid)
        except Exception as e:
            log.error(f"[reject] {pid}: {e}")
    if deleted:
        await broadcast_fn({"type": "pipeline_rejected", "ids": deleted})
        add_notification_fn(f"🗑 {len(deleted)} post(s) rejeté(s)", "info")
    return {"rejected": deleted}


# ── PHASE 4 — DRIP PUBLISHER (1/day default) ───────────────────────────

def _load_drip_state() -> dict:
    if not os.path.exists(DRIP_STATE_FILE):
        return {"published_today": 0, "day": "", "history": []}
    try:
        return json.loads(open(DRIP_STATE_FILE).read())
    except Exception:
        return {"published_today": 0, "day": "", "history": []}


def _save_drip_state(state: dict):
    state["history"] = state.get("history", [])[-200:]
    open(DRIP_STATE_FILE, "w").write(json.dumps(state, indent=2))


def _build_omnipost_post(meta: dict) -> dict:
    """Convert pipeline meta to OmniPost Post dict."""
    folder = Path(meta["_folder"])
    media = []
    # Prefer the converted vertical video for tiktok/reels-style platforms
    tiktok = folder / "tiktok.mp4"
    cover  = folder / "cover.jpg"
    if tiktok.exists():
        media.append(str(tiktok))
    elif cover.exists():
        media.append(str(cover))

    captions = meta.get("captions") or {}
    primary_platform = (meta.get("platforms") or ["instagram"])[0]
    primary_caption = captions.get(primary_platform, "")

    return {
        "id":           f"genia_{meta['id']}",
        "platforms":    list(meta.get("platforms", [])),
        "content":      primary_caption,
        "media":        media,
        "hashtags":     [],
        "scheduled":    "",
        "status":       "publishing",
        "created_at":   meta.get("created_at", datetime.now().isoformat()),
        "published_at": "",
        "results":      {},
        "title":        meta.get("title", ""),
        "tags":         [],
        "link":         meta.get("link", ""),
        "source":       "genia",
        "source_id":    meta["id"],
        "captions":     captions,  # full per-platform map
    }


async def _drip_loop(get_settings, omnipost_state, save_posts_fn, publish_post_fn,
                    broadcast_fn, add_notification_fn):
    log.info("[pipeline] drip loop started")
    while True:
        delay = 600
        try:
            cfg = {**DEFAULT_GENIA, **((get_settings() or {}).get("genia") or {})}
            delay = max(60, int(cfg.get("drip_seconds", 600)))

            now = datetime.now()
            today = now.strftime("%Y-%m-%d")

            state = _load_drip_state()
            if state.get("day") != today:
                state["day"] = today
                state["published_today"] = 0

            # Check if it's time and we haven't hit daily quota
            target_hour = int(cfg.get("drip_hour", 14))
            per_day    = int(cfg.get("drip_per_day", 1))

            if now.hour < target_hour:
                await asyncio.sleep(delay)
                continue

            if state["published_today"] >= per_day:
                await asyncio.sleep(delay)
                continue

            approved = list_phase("approved")
            if not approved:
                await asyncio.sleep(delay)
                continue

            # Take oldest approved first (FIFO — earliest approved = first published)
            approved.sort(key=lambda m: m.get("approved_at", ""))
            to_publish = approved[: max(1, per_day - state["published_today"])]

            for meta in to_publish:
                pid = meta["id"]
                op_post = _build_omnipost_post(meta)

                if cfg.get("auto_publish", True):
                    omnipost_state.posts.append(op_post)
                    save_posts_fn()
                    await broadcast_fn({"type": "post_status", "id": op_post["id"], "status": "publishing"})
                    try:
                        results = await publish_post_fn(op_post)
                        op_post["status"] = "published"
                        op_post["published_at"] = datetime.now().isoformat()
                        op_post["results"] = results
                        save_posts_fn()
                        await broadcast_fn({"type": "post_published", "post": op_post})
                        add_notification_fn(
                            f"🚀 Drip: «{meta.get('title')}» publié sur {', '.join(op_post['platforms'])}",
                            "success",
                        )
                        meta["status"] = "published"
                        meta["published_at"] = op_post["published_at"]
                        meta["results"] = results
                    except Exception as e:
                        op_post["status"] = "failed"
                        op_post["results"] = {"error": str(e)}
                        save_posts_fn()
                        meta["status"] = "failed"
                        meta["error"] = str(e)
                        log.error(f"[drip] publish {pid} failed: {e}")
                        write_meta(Path(meta["_folder"]), meta)
                        move_post(pid, "failed")
                        continue
                else:
                    # Just stage in OmniPost as a scheduled post (manual review/click in UI)
                    op_post["status"] = "scheduled"
                    op_post["scheduled"] = (datetime.now() + timedelta(minutes=5)).isoformat()
                    omnipost_state.posts.append(op_post)
                    save_posts_fn()
                    await broadcast_fn({"type": "post_created", "post": op_post})

                # Move pipeline folder to published/
                write_meta(Path(meta["_folder"]), meta)
                new_folder = move_post(pid, "published")
                if new_folder:
                    meta["_folder"] = str(new_folder)

                state["published_today"] += 1
                state["history"].append({
                    "id": pid, "title": meta.get("title"),
                    "at": datetime.now().isoformat(),
                })
                _save_drip_state(state)
        except Exception as e:
            log.error(f"[drip] loop error: {e}")
        await asyncio.sleep(delay)


# ── Public API for omnipost.py ─────────────────────────────────────────

def get_status_payload() -> dict:
    return {
        "type":   "pipeline_status",
        "counts": phase_counts(),
        "drip":   _load_drip_state(),
    }


def get_list_payload(phase: str) -> dict:
    return {
        "type":  "pipeline_list",
        "phase": phase,
        "items": list_phase(phase) if phase in PHASES else [],
    }


async def run_all_loops(get_settings, omnipost_state, save_posts_fn,
                        publish_post_fn, broadcast_fn, add_notification_fn):
    """Entry point — spawn the 3 background loops (approve is on-demand)."""
    ensure_dirs()
    log.info(f"[pipeline] root={PIPELINE_ROOT.resolve()}")
    await asyncio.gather(
        _ingest_loop(get_settings, broadcast_fn, add_notification_fn),
        _convert_loop(get_settings, broadcast_fn, add_notification_fn),
        _drip_loop(get_settings, omnipost_state, save_posts_fn, publish_post_fn,
                   broadcast_fn, add_notification_fn),
    )
