"""
OmniPost - Competitor Analyzer
Analyse complete d'un site concurrent
Usage: python competitor_analyzer.py
"""
import asyncio, json, time, re, os, urllib.request, urllib.parse, ssl, threading
from datetime import datetime

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

WS_PORT = 8870

def fetch_url(url, timeout=12):
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,*/*",
        })
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            lt   = time.time() - t0
            html = r.read().decode("utf-8", errors="ignore")
            hdrs = dict(r.headers)
            code = r.status
        return {"html": html, "headers": hdrs, "status": code, "load_time": round(lt, 3), "error": None}
    except Exception as e:
        return {"html": "", "headers": {}, "status": 0, "load_time": 0, "error": str(e)}

def get_tag(html, tag, attr=None):
    pat = f"<{tag}[^>]*>(.*?)</{tag}>"
    m = re.search(pat, html, re.I | re.S)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return ""

def get_meta(html, name):
    m = re.search(r'<meta[^>]+name=["\']' + name + r'["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']' + name + r'["\']', html, re.I)
    return m.group(1).strip() if m else ""

def get_og(html, prop):
    m = re.search(r'<meta[^>]+property=["\']og:' + prop + r'["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:' + prop + r'["\']', html, re.I)
    return m.group(1)[:300] if m else ""

def analyze_seo(html, url):
    r = {}
    title = get_tag(html, "title")
    r["title"] = title
    r["title_len"] = len(title)
    r["title_score"] = ("OK" if 30 <= len(title) <= 60 else "COURT" if len(title) < 30 and title else "LONG" if len(title) > 60 else "MANQUANT")

    desc = get_meta(html, "description")
    r["meta_desc"] = desc
    r["meta_len"] = len(desc)
    r["meta_score"] = ("OK" if 100 <= len(desc) <= 160 else "COURT" if len(desc) < 100 and desc else "LONG" if len(desc) > 160 else "MANQUANT")

    r["meta_keywords"] = get_meta(html, "keywords")
    r["robots_meta"] = get_meta(html, "robots") or "index,follow"

    for h in ["h1", "h2", "h3"]:
        tags = re.findall(f"<{h}[^>]*>(.*?)</{h}>", html, re.I | re.S)
        r[f"{h}_count"] = len(tags)
        r[f"{h}_list"]  = [re.sub(r"<[^>]+>", "", t).strip()[:80] for t in tags[:5]]

    imgs = re.findall(r"<img[^>]*>", html, re.I)
    r["img_total"]  = len(imgs)
    r["img_no_alt"] = len([i for i in imgs if "alt=" not in i.lower()])
    r["has_schema"] = "application/ld+json" in html
    r["has_og"]     = 'property="og:title"' in html or "og:title" in html
    r["has_canonical"] = 'rel="canonical"' in html

    return r

def analyze_content(html, url):
    clean = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.I | re.S)
    text  = re.sub(r"<[^>]+>", " ", clean)
    text  = re.sub(r"\s+", " ", text).strip()
    words = text.split()

    r = {
        "word_count":    len(words),
        "char_count":    len(text),
        "read_time_min": max(1, len(words) // 200),
    }

    base   = urllib.parse.urlparse(url).netloc
    links  = re.findall(r'href=["\']([^"\'#][^"\']*)["\']', html, re.I)
    intern = [l for l in links if base in l or l.startswith("/")]
    extern = [l for l in links if l.startswith("http") and base not in l]
    r["links_internal"] = len(intern)
    r["links_external"] = len(extern)
    r["external_list"]  = list(set(extern))[:8]

    stop = set("le la les de du des un une en et est que qui dans pour par sur au aux avec il elle ils elles nous vous ce se sa son ses leur leurs the and is in of to a that it with for on as".split())
    freq = {}
    for w in words:
        w2 = w.lower().strip(".,!?;:()")
        if len(w2) > 3 and w2 not in stop:
            freq[w2] = freq.get(w2, 0) + 1
    r["top_keywords"] = sorted(freq.items(), key=lambda x: -x[1])[:15]

    r["has_form"]  = bool(re.search(r"<form[^>]*>", html, re.I))
    r["has_video"] = bool(re.search(r"<video|youtube\.com/embed|vimeo\.com", html, re.I))
    r["has_chat"]  = bool(re.search(r"intercom|crisp|tawk|zendesk|livechat", html, re.I))
    r["has_popup"] = bool(re.search(r"popup|modal|newsletter", html, re.I))
    r["has_blog"]  = bool(re.search(r"/blog|/articles|/news", html, re.I))

    emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    r["emails"] = list(set(emails))[:3]

    return r

def analyze_tech(html, headers):
    tech = []
    hl = html.lower()
    hs = " ".join(str(v) for v in headers.values()).lower()

    cat_icons = {"CMS":"📝","E-commerce":"🛍","Framework":"⚛️","Analytics":"📊",
                 "Email":"📧","CRM":"🟠","Paiement":"💳","Pub":"📢",
                 "CDN":"☁️","Hosting":"🖥","Chat":"💬","Serveur":"🖥"}

    def add(name, cat, cond):
        if cond:
            tech.append({"name": name, "cat": cat, "icon": cat_icons.get(cat, "🔧")})

    add("WordPress",     "CMS",         "wp-content" in hl or "wp-includes" in hl)
    add("Shopify",       "E-commerce",  "cdn.shopify" in hl)
    add("Wix",           "CMS",         "wix.com" in hl or "wixstatic" in hl)
    add("Squarespace",   "CMS",         "squarespace" in hl)
    add("Webflow",       "CMS",         "webflow" in hl)
    add("Drupal",        "CMS",         "drupal" in hl)
    add("Joomla",        "CMS",         "joomla" in hl)
    add("WooCommerce",   "E-commerce",  "woocommerce" in hl)
    add("Magento",       "E-commerce",  "magento" in hl)
    add("PrestaShop",    "E-commerce",  "prestashop" in hl)
    add("BigCommerce",   "E-commerce",  "bigcommerce" in hl)
    add("React",         "Framework",   "__react" in hl or "react.development" in hl)
    add("Vue.js",        "Framework",   "__vue" in hl or "vue.min.js" in hl)
    add("Angular",       "Framework",   "ng-version" in hl)
    add("Next.js",       "Framework",   "__next" in hl or "_next/static" in hl)
    add("jQuery",        "Framework",   "jquery" in hl)
    add("Google Anal.",  "Analytics",   "google-analytics.com" in hl or "gtag(" in hl)
    add("GTM",           "Analytics",   "googletagmanager.com" in hl)
    add("Hotjar",        "Analytics",   "hotjar" in hl)
    add("Mixpanel",      "Analytics",   "mixpanel" in hl)
    add("Plausible",     "Analytics",   "plausible.io" in hl)
    add("Mailchimp",     "Email",       "mailchimp" in hl)
    add("Klaviyo",       "Email",       "klaviyo" in hl)
    add("HubSpot",       "CRM",         "hubspot" in hl)
    add("Stripe",        "Paiement",    "stripe" in hl)
    add("PayPal",        "Paiement",    "paypal" in hl)
    add("FB Pixel",      "Pub",         "facebook.com/tr?" in hl)
    add("TikTok Pixel",  "Pub",         "analytics.tiktok.com" in hl)
    add("Cloudflare",    "CDN",         "cloudflare" in hs)
    add("Vercel",        "Hosting",     "vercel" in hs)
    add("Netlify",       "Hosting",     "netlify" in hs)
    add("AWS",           "Hosting",     "amazonaws" in hs)
    add("Intercom",      "Chat",        "intercom" in hl)
    add("Crisp",         "Chat",        "crisp.chat" in hl)

    srv = headers.get("Server", "") or headers.get("server", "")
    if srv:
        tech.append({"name": srv.split("/")[0], "cat": "Serveur", "icon": "🖥"})

    return tech

def analyze_perf(html, load_time, headers):
    scripts  = len(re.findall(r'<script[^>]+src=', html, re.I))
    styles   = len(re.findall(r'<link[^>]+stylesheet', html, re.I))
    images   = len(re.findall(r'<img[^>]*>', html, re.I))
    lazy_img = len(re.findall(r'loading=["\']lazy["\']', html, re.I))
    size_kb  = round(len(html.encode()) / 1024, 1)
    h = {k.lower(): v for k, v in headers.items()}
    gzip = "gzip" in h.get("content-encoding", "").lower()

    score = 100
    tips  = []
    if load_time > 3:    score -= 35; tips.append("Chargement critique >3s")
    elif load_time > 1:  score -= 15; tips.append("Chargement lent >1s")
    else:                tips.append("Bon chargement <1s")
    if size_kb > 500:    score -= 15; tips.append(f"HTML lourd {size_kb}KB")
    if scripts > 12:     score -= 10; tips.append(f"Trop de scripts ({scripts})")
    if not gzip:         score -= 10; tips.append("Compression gzip absente")
    if lazy_img == 0 and images > 5: score -= 5; tips.append("Pas de lazy loading")

    return {
        "load_ms": round(load_time * 1000),
        "size_kb": size_kb,
        "scripts": scripts,
        "styles":  styles,
        "images":  images,
        "lazy":    lazy_img,
        "gzip":    gzip,
        "cache":   h.get("cache-control", "Non defini")[:80],
        "score":   max(0, score),
        "tips":    tips,
    }

def analyze_security(headers, url):
    h = {k.lower(): v for k, v in headers.items()}
    checks = {
        "HTTPS":           url.startswith("https"),
        "HSTS":            "strict-transport-security" in h,
        "CSP":             "content-security-policy" in h,
        "X-Frame-Options": "x-frame-options" in h,
        "X-XSS-Protect":   "x-xss-protection" in h,
        "X-Content-Type":  "x-content-type-options" in h,
        "Referrer-Policy": "referrer-policy" in h,
    }
    score = sum(checks.values())
    grade = "A" if score >= 6 else "B" if score >= 4 else "C" if score >= 2 else "D"
    return {"checks": checks, "score": score, "max": 7, "grade": grade}

def analyze_social(html, url):
    og = {p: get_og(html, p) for p in ["title","description","image","url","type","site_name"]}

    tw = {}
    for tag in ["card","title","description"]:
        m = re.search(r'<meta[^>]+name=["\']twitter:' + tag + r'["\'][^>]+content=["\']([^"\']*)["\']', html, re.I)
        tw[tag] = m.group(1)[:200] if m else ""

    pats = {
        "Facebook":  r'facebook\.com/(?!share|sharer|tr)[\w\./-]+',
        "Instagram": r'instagram\.com/[\w\.]+',
        "TikTok":    r'tiktok\.com/@[\w\.]+',
        "YouTube":   r'youtube\.com/(?:channel|user|c|@)[\w/-]+',
        "Twitter":   r'(?:twitter|x)\.com/[\w]+',
        "LinkedIn":  r'linkedin\.com/(?:company|in)/[\w-]+',
        "Pinterest": r'pinterest\.com/[\w]+',
    }
    found = {}
    for plat, pat in pats.items():
        m = re.search(pat, html, re.I)
        if m:
            found[plat] = "https://" + m.group(0)

    return {"og": og, "twitter": tw, "social_links": found}

def analyze_ecommerce(html, tech):
    hl = html.lower()
    r  = {"is_ecom": False, "signals": [], "traffic_estimate": ""}

    ecom = [t for t in tech if t["cat"] == "E-commerce"]
    pay  = [t for t in tech if t["cat"] == "Paiement"]
    if ecom: r["is_ecom"] = True; r["signals"].append(f"Plateforme: {ecom[0]['name']}")
    if pay:  r["is_ecom"] = True; r["signals"].append(f"Paiement: {', '.join(t['name'] for t in pay)}")

    cart   = len(re.findall(r"add.to.cart|ajouter.au.panier|buy now|acheter", hl))
    prices = len(re.findall(r"[\$\€]\d+|\d+\s*[\$\€]", html))
    revs   = len(re.findall(r"review|avis client|rating|etoile", hl))
    trust  = len(re.findall(r"garantie|certifie|secure|remboursé", hl))

    if cart:   r["is_ecom"] = True; r["signals"].append(f"{cart} bouton(s) achat")
    if prices: r["signals"].append(f"{prices} prix detectes")
    if revs > 3: r["signals"].append("Systeme d'avis clients")
    if trust:  r["signals"].append(f"{trust} badge(s) confiance")

    has_cdn = any(t["name"] in ["Cloudflare","Fastly"] for t in tech)
    has_ga  = any("Analytics" in t["cat"] for t in tech)
    has_crm = any(t["name"] in ["HubSpot","Klaviyo","Mailchimp"] for t in tech)

    if has_cdn and has_crm:  r["traffic_estimate"] = "Tres fort — +100k visites/mois"
    elif has_cdn and has_ga: r["traffic_estimate"] = "Fort — +10k visites/mois"
    elif has_ga:             r["traffic_estimate"] = "Moyen — 1k-10k visites/mois"
    else:                    r["traffic_estimate"] = "Faible ou nouveau site"

    return r

async def analyze_site(url, ws=None):
    if not url.startswith("http"):
        url = "https://" + url

    async def emit(msg):
        if ws:
            try: await ws.send(json.dumps(msg, ensure_ascii=False))
            except Exception: pass

    await emit({"type":"progress","pct":10,"step":"Connexion au site..."})
    page = fetch_url(url)
    if page["error"]:
        await emit({"type":"error","error":f"Impossible: {page['error']}"})
        return

    html = page["html"]; headers = page["headers"]

    await emit({"type":"progress","pct":25,"step":"Analyse SEO..."})
    seo = analyze_seo(html, url)

    await emit({"type":"progress","pct":40,"step":"Analyse contenu..."})
    content = analyze_content(html, url)

    await emit({"type":"progress","pct":55,"step":"Detection technologies..."})
    tech = analyze_tech(html, headers)

    await emit({"type":"progress","pct":68,"step":"Analyse performance..."})
    perf = analyze_perf(html, page["load_time"], headers)

    await emit({"type":"progress","pct":78,"step":"Analyse securite..."})
    security = analyze_security(headers, url)

    await emit({"type":"progress","pct":88,"step":"Reseaux sociaux..."})
    social = analyze_social(html, url)

    await emit({"type":"progress","pct":94,"step":"E-commerce..."})
    ecom = analyze_ecommerce(html, tech)

    await emit({"type":"progress","pct":98,"step":"Robots & Sitemap..."})
    base    = urllib.parse.urlparse(url).scheme + "://" + urllib.parse.urlparse(url).netloc
    robots  = fetch_url(base + "/robots.txt", timeout=5)
    sitemap = fetch_url(base + "/sitemap.xml", timeout=5)

    await emit({"type":"progress","pct":100,"step":"Termine!"})
    await emit({
        "type":        "result",
        "url":         url,
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "seo":         seo,
        "content":     content,
        "tech":        tech,
        "perf":        perf,
        "security":    security,
        "social":      social,
        "ecom":        ecom,
        "has_robots":  robots["status"] == 200,
        "has_sitemap": sitemap["status"] == 200,
        "robots_txt":  robots["html"][:500] if robots["status"] == 200 else "",
    })

async def ws_handler(websocket):
    async for raw in websocket:
        try:
            msg = json.loads(raw)
            if msg.get("cmd") == "analyze":
                url = msg.get("url", "").strip()
                if url:
                    asyncio.create_task(analyze_site(url, websocket))
        except Exception as e:
            try: await websocket.send(json.dumps({"type":"error","error":str(e)}))
            except Exception: pass

async def main_async():
    if not HAS_WS:
        print("pip install websockets"); return
    async with websockets.serve(ws_handler, "localhost", WS_PORT):
        print(f"[OK] Competitor Analyzer ws://localhost:{WS_PORT}")
        def open_b():
            time.sleep(1.5)
            import webbrowser, sys
            base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            p = os.path.join(base_dir, "competitor_analyzer.html")
            if not os.path.exists(p):
                p = os.path.join(os.path.dirname(sys.executable), "competitor_analyzer.html")
            webbrowser.open(f"file:///{p.replace(os.sep, '/')}")
        threading.Thread(target=open_b, daemon=True).start()
        await asyncio.Future()

def main():
    print("\n  OmniPost — Competitor Analyzer\n  SEO + Tech + Perf + Securite + E-commerce\n")
    try: asyncio.run(main_async())
    except KeyboardInterrupt: print("Arrete")

if __name__ == "__main__":
    main()
