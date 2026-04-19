#!/usr/bin/env python3
"""GIa Pipeline Dashboard - visual approve/reject UI on http://0.0.0.0:8862"""
import http.server, json, os, shutil, socketserver, urllib.parse
import urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

ROOT = Path('/srv/omnipost/pipeline')
PHASES = ['created', 'converted', 'approved', 'published', 'failed']
PORT = 8862

# GeniA API config (set via systemd Environment=)
GENIA_API = os.environ.get('GENIA_API_URL', 'https://api.genia.social')
GENIA_KEY = os.environ.get('GENIA_SERVICE_KEY', '')


def _genia_request(method, path, body=None, timeout=15):
    url = GENIA_API.rstrip('/') + path
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'GIa-Approver/1.0',
    }
    if GENIA_KEY:
        headers['Authorization'] = 'Bearer ' + GENIA_KEY
    data = json.dumps(body).encode('utf-8') if body is not None else None
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


# ── Drip schedule helpers ──────────────────────────────────────────────

OMNIPOST_SETTINGS = '/srv/omnipost/omnipost_settings.json'
DRIP_STATE = '/srv/omnipost/pipeline_drip_state.json'
OMNIPOST_POSTS = '/srv/omnipost/omnipost_posts.json'


def _read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def get_schedule_payload():
    """Return next drip time + approved queue + recent published with platform results."""
    settings = _read_json(OMNIPOST_SETTINGS, {})
    genia_cfg = settings.get('genia', {}) or {}
    drip_per_day = int(genia_cfg.get('drip_per_day', 1))
    drip_hour = int(genia_cfg.get('drip_hour', 14))
    platforms = list(genia_cfg.get('platforms', []))
    auto_publish = bool(genia_cfg.get('auto_publish', False))

    # Next drip time (today @ drip_hour if not yet, else tomorrow)
    now = datetime.now()
    today_drip = now.replace(hour=drip_hour, minute=0, second=0, microsecond=0)
    drip_state = _read_json(DRIP_STATE, {})
    today_str = now.strftime('%Y-%m-%d')
    published_today = drip_state.get('published_today', 0) if drip_state.get('day') == today_str else 0
    quota_left = max(0, drip_per_day - published_today)

    if quota_left > 0 and now < today_drip:
        next_drip = today_drip
    elif quota_left > 0 and now >= today_drip:
        # Drip-eligible NOW (within current day window)
        next_drip = now.replace(minute=(now.minute // 10 + 1) * 10 % 60, second=0, microsecond=0)
    else:
        # Tomorrow
        from datetime import timedelta as _td
        next_drip = (today_drip + _td(days=1))

    # Approved queue (FIFO oldest first — that's what drip picks)
    approved = list_phase('approved')
    approved.sort(key=lambda m: m.get('approved_at', ''))

    # Recent published — collect from pipeline/published meta + omnipost_posts.json results
    published_meta = list_phase('published')
    op_posts = _read_json(OMNIPOST_POSTS, [])
    op_by_source = {}
    for p in (op_posts if isinstance(op_posts, list) else []):
        sid = p.get('source_id') or p.get('id', '').replace('genia_', '')
        if sid:
            op_by_source[sid] = p

    recent = []
    for m in sorted(published_meta, key=lambda x: x.get('published_at', ''), reverse=True)[:30]:
        op = op_by_source.get(m['id'], {})
        results = op.get('results') or m.get('results') or {}
        platform_links = []
        for plat, res in (results.items() if isinstance(results, dict) else []):
            if not isinstance(res, dict):
                continue
            url = res.get('url') or res.get('shorts_url')
            if not url and plat == 'facebook' and res.get('id'):
                url = 'https://www.facebook.com/' + str(res['id']).replace('_', '/posts/')
            platform_links.append({
                'platform': plat,
                'status': res.get('status', 'unknown'),
                'url': url,
                'error': res.get('error'),
            })
        recent.append({
            'id': m['id'],
            'title': m.get('title', '')[:80],
            'published_at': m.get('published_at', ''),
            'platforms': platform_links,
            'genia_link': m.get('link', ''),
        })

    return {
        'config': {
            'auto_publish': auto_publish,
            'drip_per_day': drip_per_day,
            'drip_hour_utc': drip_hour,
            'platforms': platforms,
            'genia_enabled': bool(genia_cfg.get('enabled')),
        },
        'today': {
            'date': today_str,
            'published_today': published_today,
            'quota_left': quota_left,
        },
        'next_drip_iso': next_drip.isoformat(),
        'next_drip_human': next_drip.strftime('%a %d %b %H:%M UTC'),
        'queue_count': len(approved),
        'queue_next': [
            {
                'id': m['id'],
                'title': m.get('title', '')[:60],
                'approved_at': m.get('approved_at', ''),
            }
            for m in approved[:10]
        ],
        'recent_published': recent,
    }

HTML = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>GIa Pipeline</title>
<meta http-equiv="refresh" content="60">
<style>
*{box-sizing:border-box;font-family:system-ui,sans-serif}
body{background:#0a0a0a;color:#eee;margin:0;padding:0}
header{background:#1a0606;padding:14px 20px;border-bottom:2px solid #dc2626;display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:100}
header h1{margin:0;color:#dc2626;font-size:20px}
header .counts{display:flex;gap:14px;font-size:12px;margin-left:auto}
header .counts span{background:#222;padding:4px 10px;border-radius:6px}
header .counts .approved{background:#065f46;color:#a7f3d0}
header .counts .converted{background:#92400e;color:#fde68a}
header .counts .published{background:#1e3a8a;color:#bfdbfe}
header .counts .failed{background:#7f1d1d}
nav{padding:12px 20px;background:#161616;display:flex;gap:8px;flex-wrap:wrap}
nav a{padding:6px 12px;background:#222;color:#aaa;text-decoration:none;border-radius:6px;font-size:13px}
nav a.active{background:#dc2626;color:#fff}
.bulk{padding:14px 20px;background:#161616;display:flex;gap:10px;border-bottom:1px solid #222}
.bulk button{padding:8px 16px;border:0;border-radius:6px;cursor:pointer;font-weight:bold;font-size:13px}
.bulk .approve{background:#10b981;color:#fff}
.bulk .reject{background:#dc2626;color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;padding:20px}
.card{background:#161616;border:1px solid #2a2a2a;border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card video,.card img{width:100%;height:380px;object-fit:cover;background:#000;display:block}
.card .body{padding:10px 12px;flex:1;display:flex;flex-direction:column;gap:6px}
.card .title{font-weight:bold;font-size:13px;color:#dc2626;margin:0}
.card .meta{font-size:10px;color:#666}
.card .cap{font-size:11px;color:#aaa;line-height:1.4;max-height:60px;overflow:hidden}
.card .actions{display:flex;gap:6px;padding:8px;background:#0e0e0e;border-top:1px solid #2a2a2a}
.card .actions button{flex:1;padding:6px;border:0;border-radius:5px;cursor:pointer;font-size:12px;font-weight:bold}
.card .approve{background:#10b981;color:#fff}
.card .reject{background:#dc2626;color:#fff}
.card .open{background:#444;color:#fff;text-align:center;text-decoration:none;padding:6px;line-height:1.2}
.empty{text-align:center;padding:60px 20px;color:#555}
.empty h2{color:#dc2626}
</style></head><body>
<header>
  <h1>&#129304; GIa Pipeline</h1>
  <div class="counts" id="counts"></div>
</header>
<nav id="nav"></nav>
<div class="bulk">
  <button class="approve" onclick="bulkApprove()">&#9989; Tout approuver (cette vue)</button>
  <button class="reject" onclick="bulkReject()">&#128465; Tout rejeter (cette vue)</button>
  <span style="margin-left:auto;font-size:11px;color:#666;align-self:center">Auto-refresh 60s</span>
</div>
<div id="grid" class="grid"></div>

<script>
const params = new URLSearchParams(location.search);
const phase = params.get('phase') || 'converted';
const PHASES = ['created','converted','approved','published','failed'];

async function loadCounts(){
  const r = await fetch('/api/status');
  const d = await r.json();
  const counts = d.counts;
  document.getElementById('nav').innerHTML = PHASES.map(p =>
    `<a class="${p===phase?'active':''}" href="?phase=${p}">${p} (${counts[p]||0})</a>`
  ).join('');
  document.getElementById('counts').innerHTML = PHASES.map(p =>
    `<span class="${p}">${p}: ${counts[p]||0}</span>`
  ).join('');
}

async function loadList(){
  const r = await fetch('/api/list?phase=' + phase);
  const d = await r.json();
  const grid = document.getElementById('grid');
  if(!d.items || d.items.length===0){
    grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><h2>Vide</h2><p>Aucun post dans la phase "' + phase + '"</p></div>';
    return;
  }
  grid.innerHTML = d.items.map(p => {
    const media = p.has_tiktok
      ? `<video src="/media/${phase}/${p.id}/tiktok.mp4" controls muted preload="metadata"></video>`
      : `<img src="/media/${phase}/${p.id}/cover.jpg" loading="lazy">`;
    const actions =
      (phase==='converted' ? `<button class="approve" onclick="approve('${p.id}')">&#9989; Approuver</button>` : '') +
      (phase!=='published' ? `<button class="reject" onclick="reject('${p.id}')">&#128465; Rejeter</button>` : '') +
      `<a class="open" href="${p.link||'#'}" target="_blank">&#8599; Source</a>`;
    return `<div class="card" id="c-${p.id}">
      ${media}
      <div class="body">
        <p class="title">${(p.title||'Untitled').replace(/</g,'&lt;')}</p>
        <p class="meta">${p.id.slice(0,8)} &middot; ${(p.created_at||'').slice(0,16)}</p>
        <p class="cap">${(p.cap||'').replace(/</g,'&lt;')}</p>
      </div>
      <div class="actions">${actions}</div>
    </div>`;
  }).join('');
}

async function approve(id){
  await fetch('/api/approve?id=' + id, {method:'POST'});
  document.getElementById('c-'+id).style.opacity = '0.3';
  setTimeout(refresh, 400);
}
async function reject(id){
  if(!confirm('Supprimer definitivement ?')) return;
  await fetch('/api/reject?id=' + id, {method:'POST'});
  document.getElementById('c-'+id).style.opacity = '0.3';
  setTimeout(refresh, 400);
}
async function bulkApprove(){
  if(phase!=='converted'){ alert('Approuvable seulement depuis "converted"'); return; }
  if(!confirm('Approuver TOUS les posts visibles ?')) return;
  await fetch('/api/approve_all', {method:'POST'});
  refresh();
}
async function bulkReject(){
  if(phase==='published'){ alert('On ne supprime pas les publies'); return; }
  if(!confirm('SUPPRIMER TOUS les posts visibles ? Action irreversible.')) return;
  await fetch('/api/reject_all?phase=' + phase, {method:'POST'});
  refresh();
}
function refresh(){ loadCounts(); loadList(); }
refresh();
</script>
</body></html>
"""


def list_phase(phase):
    base = ROOT / phase
    if not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta_f = d / 'meta.json'
        if not meta_f.exists():
            continue
        try:
            m = json.loads(meta_f.read_text(encoding='utf-8'))
        except Exception:
            continue
        out.append({
            'id': m.get('id', d.name),
            'title': m.get('title', '')[:80],
            'created_at': m.get('created_at', ''),
            'link': m.get('link', ''),
            'cap': ((m.get('source_post') or {}).get('caption') or '')[:200],
            'has_tiktok': (d / 'tiktok.mp4').exists(),
            'has_cover': (d / 'cover.jpg').exists(),
        })
    return out


def find_post(post_id):
    for ph in PHASES:
        p = ROOT / ph / post_id
        if p.exists():
            return ph, p
    return None, None


def move_post(post_id, to_phase):
    ph, src = find_post(post_id)
    if not src:
        return None
    dest = ROOT / to_phase / post_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))
    mf = dest / 'meta.json'
    if mf.exists():
        try:
            m = json.loads(mf.read_text(encoding='utf-8'))
            m['status'] = to_phase
            m[to_phase + '_at'] = datetime.now().isoformat()
            mf.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass
    return dest


def reject_post(post_id):
    ph, src = find_post(post_id)
    if src:
        shutil.rmtree(src)
        return True
    return False


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **k):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: Path, content_type='application/octet-stream'):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        with open(path, 'rb') as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ('/', '/index.html'):
            body = HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == '/api/status':
            counts = {p: len([d for d in (ROOT / p).iterdir() if d.is_dir()]) if (ROOT / p).exists() else 0 for p in PHASES}
            return self._json({'counts': counts})
        if u.path == '/api/list':
            phase = (q.get('phase') or ['converted'])[0]
            return self._json({'items': list_phase(phase) if phase in PHASES else []})
        if u.path.startswith('/media/'):
            parts = u.path.lstrip('/').split('/')
            if len(parts) >= 4:
                phase, pid, fname = parts[1], parts[2], parts[3]
                if phase in PHASES and '..' not in pid and '..' not in fname:
                    fpath = ROOT / phase / pid / fname
                    ct = 'video/mp4' if fname.endswith('.mp4') else 'image/jpeg'
                    return self._serve_file(fpath, ct)
            self.send_error(404)
            return
        if u.path == '/api/genia/posts':
            limit = int((q.get('limit') or ['50'])[0])
            offset = int((q.get('offset') or ['0'])[0])
            code, data = genia_list_posts(limit=limit, offset=offset)
            return self._json({'status_code': code, 'items': data if isinstance(data, list) else []})
        if u.path == '/api/schedule':
            return self._json(get_schedule_payload())
        self.send_error(404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/api/approve':
            pid = (q.get('id') or [''])[0]
            return self._json({'ok': move_post(pid, 'approved') is not None})
        if u.path == '/api/reject':
            pid = (q.get('id') or [''])[0]
            return self._json({'ok': reject_post(pid)})
        if u.path == '/api/approve_all':
            ids = [m['id'] for m in list_phase('converted')]
            for pid in ids:
                move_post(pid, 'approved')
            return self._json({'count': len(ids)})
        if u.path == '/api/reject_all':
            phase = (q.get('phase') or ['converted'])[0]
            if phase not in PHASES or phase == 'published':
                return self._json({'error': 'forbidden'}, 400)
            ids = [m['id'] for m in list_phase(phase)]
            for pid in ids:
                reject_post(pid)
            return self._json({'count': len(ids)})
        if u.path == '/api/genia/posts/delete':
            pid = (q.get('id') or [''])[0]
            code, data = genia_delete_post(pid)
            return self._json({'status_code': code, 'result': data}, code=200 if code in (200, 204) else 400)
        self.send_error(404)


if __name__ == '__main__':
    print(f'GIa Pipeline Dashboard sur http://0.0.0.0:{PORT}')
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', PORT), Handler) as httpd:
        httpd.serve_forever()
