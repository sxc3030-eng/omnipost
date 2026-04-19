#!/usr/bin/env python3
"""Patch genia_pipeline.py: fix text overflow in TikTok video generation.
- Smaller fonts to fit 1080px width
- Auto-wrap on multiple lines
- Hard truncation per line
- More margin from edges
"""
import ast
from pathlib import Path

PATH = Path('/srv/omnipost/genia_pipeline.py')
src = PATH.read_text()

# Replace _sanitize_text with a version that supports wrapping
OLD_SANI = '''def _sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[\\r\\n]+", " ", str(s))
    s = s.replace("\\\\", "").replace(":", "\\\\:").replace("'", "")
    return s.strip()[:80]'''

NEW_SANI = '''def _sanitize_text(s: str, maxlen: int = 80) -> str:
    if not s:
        return ""
    s = re.sub(r"[\\r\\n]+", " ", str(s))
    s = s.replace("\\\\", "").replace(":", "\\\\:").replace("'", "")
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
    return chr(10).join(lines)'''

if OLD_SANI in src:
    src = src.replace(OLD_SANI, NEW_SANI, 1)
    print("Replaced _sanitize_text + added _wrap_text")
elif "_wrap_text" not in src:
    raise SystemExit("Could not find _sanitize_text to replace")
else:
    print("Already patched, skipping helper replacement")

# Replace the drawtext block with smaller fonts + wrapped text
OLD_DRAW = '''        artist_t = _sanitize_text(meta.get("title") or "")
        caption_raw = (meta.get("source_post") or {}).get("caption") or ""
        title_t  = _sanitize_text(caption_raw[:60])

        drawtext = (
            f",drawtext=fontfile={font}:text='{artist_t}':"
            f"fontsize=64:fontcolor=white:bordercolor=black:borderw=4:"
            f"x=(w-text_w)/2:y=120"
            f",drawtext=fontfile={font}:text='{title_t}':"
            f"fontsize=42:fontcolor=#DC2626:bordercolor=black:borderw=3:"
            f"x=(w-text_w)/2:y=h-220"
            f",drawtext=fontfile={font}:text='GIa Underground':"
            f"fontsize=28:fontcolor=white@0.7:"
            f"x=(w-text_w)/2:y=h-100"
        )'''

NEW_DRAW = '''        # Title (artist) at top, wrapped to 2 lines, max ~22 chars/line at fontsize 56
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
            f"box=1:boxcolor=black@0.55:boxborderw=20:line_spacing=12:"
            f"x=(w-text_w)/2:y=160"
            f",drawtext=fontfile={font}:text='{title_t}':"
            f"fontsize=36:fontcolor=#DC2626:bordercolor=black:borderw=3:"
            f"box=1:boxcolor=black@0.55:boxborderw=16:line_spacing=10:"
            f"x=(w-text_w)/2:y=h-360"
            f",drawtext=fontfile={font}:text='GIa Underground':"
            f"fontsize=26:fontcolor=white@0.75:"
            f"x=(w-text_w)/2:y=h-180"
        )'''

if OLD_DRAW in src:
    src = src.replace(OLD_DRAW, NEW_DRAW, 1)
    print("Replaced drawtext block with wrapped + smaller text")
else:
    print("WARN: drawtext block not found verbatim - skipping")

# Validate
ast.parse(src)
PATH.write_text(src)
print("genia_pipeline.py patched")
