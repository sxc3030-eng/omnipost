#!/usr/bin/env python3
"""Patch omnipost.py: replace _post_youtube with real Data API v3 upload.
Uses pure string ops (no regex replacement) to avoid escape issues."""
import ast
from pathlib import Path

PATH = Path('/srv/omnipost/omnipost.py')
src = PATH.read_text()

NEW_FUNC = '''async def _post_youtube(token: str, title: str, description: str, media: list) -> dict:
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
'''

# Find start and end of the existing function (or its broken remains) by string search
START_MARKER = 'async def _post_youtube('
END_MARKER = 'async def _post_pinterest('

start = src.find(START_MARKER)
end = src.find(END_MARKER)
if start < 0 or end < 0 or end < start:
    raise SystemExit(f"Markers not found: start={start}, end={end}")

new_src = src[:start] + NEW_FUNC + '\n' + src[end:]

# Validate syntax before writing
try:
    ast.parse(new_src)
except SyntaxError as e:
    print("SYNTAX ERROR in patched source:", e)
    print("Around line:", e.lineno)
    raise

PATH.write_text(new_src)
print("Patched _post_youtube cleanly")
