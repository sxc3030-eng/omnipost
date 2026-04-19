#!/usr/bin/env python3
"""Patch pipeline_dashboard.py: add /api/genia/posts read+delete endpoints."""
import ast
from pathlib import Path

PATH = Path('/srv/omnipost/pipeline_dashboard.py')
src = PATH.read_text()

# 1. Add helper module imports + GENIA constants near the top
INSERT_AFTER = "import http.server, json, os, shutil, socketserver, urllib.parse"
NEW_IMPORTS = """import http.server, json, os, shutil, socketserver, urllib.parse
import urllib.request, urllib.error
GENIA_API = os.environ.get('GENIA_API_URL', 'https://api.genia.social')
GENIA_KEY = os.environ.get('GENIA_SERVICE_KEY', '')"""

if INSERT_AFTER in src and "GENIA_API" not in src:
    src = src.replace(INSERT_AFTER, NEW_IMPORTS, 1)
    print("Added GENIA imports/constants")

# 2. Add helper functions right before "class Handler"
HELPERS = '''
# ---- GeniA API helpers ----
def _genia_request(method, path, body=None, timeout=15):
    url = GENIA_API.rstrip('/') + path
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'GIa-Approver/1.0',
    }
    if GENIA_KEY:
        headers['Authorization'] = 'Bearer ' + GENIA_KEY
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read().decode('utf-8')
            return r.status, (json.loads(content) if content.strip() else None)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8')
        except Exception:
            err_body = ''
        return e.code, {'error': err_body[:300]}
    except Exception as e:
        return 0, {'error': str(e)}


def genia_list_posts(limit=50, offset=0):
    qs = urllib.parse.urlencode({
        'select': 'id,type,caption,media_url,thumbnail_url,audio_url,created_at,user_id',
        'order': 'created_at.desc',
        'limit': str(limit),
        'offset': str(offset),
    })
    code, data = _genia_request('GET', '/rest/posts?' + qs)
    return code, (data or [])


def genia_delete_post(post_id):
    if not post_id:
        return 400, {'error': 'missing id'}
    qs = urllib.parse.urlencode({'id': 'eq.' + post_id})
    return _genia_request('DELETE', '/rest/posts?' + qs)


'''
if "_genia_request" not in src:
    marker = "class Handler("
    if marker in src:
        src = src.replace(marker, HELPERS + marker, 1)
        print("Added GeniA helpers")

# 3. Add new GET routes (api/genia/posts) inside do_GET — before the final send_error(404)
GET_ROUTES = """        if u.path == '/api/genia/posts':
            limit = int((q.get('limit') or ['50'])[0])
            offset = int((q.get('offset') or ['0'])[0])
            code, data = genia_list_posts(limit=limit, offset=offset)
            return self._json({'status_code': code, 'items': data if isinstance(data, list) else []})
        """
GET_MARKER = "        self.send_error(404)\n\n    def do_POST(self):"
if "/api/genia/posts" not in src:
    src = src.replace(GET_MARKER, GET_ROUTES + GET_MARKER, 1)
    print("Added GET /api/genia/posts route")

# 4. Add DELETE route
DELETE_ROUTES = """        if u.path == '/api/genia/posts/delete':
            pid = (q.get('id') or [''])[0]
            code, data = genia_delete_post(pid)
            return self._json({'status_code': code, 'result': data}, code=200 if code in (200, 204) else 400)
        """
POST_MARKER = "        self.send_error(404)\n\n\nif __name__ == '__main__':"
if "/api/genia/posts/delete" not in src:
    src = src.replace(POST_MARKER, DELETE_ROUTES + POST_MARKER, 1)
    print("Added POST /api/genia/posts/delete route")

# Validate
ast.parse(src)
PATH.write_text(src)
print("pipeline_dashboard.py patched cleanly")
