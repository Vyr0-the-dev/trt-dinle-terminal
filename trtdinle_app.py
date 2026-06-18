"""
TRT Dinle - Modern Terminal Player v7 (cmus/ncmpcpp esinli)
- Sütunlu listeler: No · Başlık · Sanatçı · Süre
- Animasyonlu spektrum (#vis, 15fps)
- ncmpcpp tarzı progress bar (━●─)
- Persistent nowbar (dock bottom)
- Kategoriler ilk sırada
- In-memory cache + thread-safe engine
- Uyku zamanlayıcısı
"""

import json, math, os, random, re, sys, time, threading
from pathlib import Path
from urllib.parse import urljoin, urlparse

os.environ["PATH"] = os.path.dirname(os.path.abspath(__file__)) + os.pathsep + os.environ.get("PATH", "")

import requests
from bs4 import BeautifulSoup

try:
    import mpv
    MPV_AVAILABLE = True
except Exception:
    mpv = None
    MPV_AVAILABLE = False

class MockMPV:
    def __init__(self, **kwargs):
        self.volume = 85
        self.playback_time = 0.0
        self.duration = 0.0
        self.pause = True
        self.mute = False
        self._thread = None
        self._running = True
        self._ended_callback = None
        threading.Thread(target=self._tick_loop, daemon=True).start()

    def _tick_loop(self):
        while self._running:
            time.sleep(0.5)
            if not self.pause and self.duration > 0:
                self.playback_time = min(self.duration, self.playback_time + 0.5)
                if self.playback_time >= self.duration:
                    self.pause = True
                    if self._ended_callback:
                        try:
                            # Mimic the mpv Event structure
                            class MockEvent:
                                def __init__(self):
                                    self.reason = "eof"
                            self._ended_callback(MockEvent())
                        except Exception:
                            pass

    def command(self, cmd, *args):
        if cmd == "loadfile":
            self.playback_time = 0.0
            self.duration = 180.0  # Simulated duration of 3 minutes
            self.pause = False
        elif cmd == "stop":
            self.playback_time = 0.0
            self.duration = 0.0
            self.pause = True

    def seek(self, secs, reference="relative"):
        if reference == "relative":
            self.playback_time = max(0.0, min(self.duration, self.playback_time + secs))

    def event_callback(self, name):
        def decorator(func):
            if name == "end_file":
                self._ended_callback = func
            return func
        return decorator

    def terminate(self):
        self._running = False

    def __setitem__(self, key, value):
        if key == "volume":
            self.volume = value

    def __getitem__(self, key):
        if key == "volume":
            return self.volume
        return None
from bs4 import BeautifulSoup

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    Button, Footer, Header, Input, Label, ListItem, ListView, LoadingIndicator, Static,
)

BASE_URL = "https://www.trtdinle.com"
HISTORY_FILE = Path.home() / ".trtdinle_history.json"
CACHE_FILE = Path.home() / ".trtdinle_cache.json"
FAVORITES_FILE = Path.home() / ".trtdinle_favorites.json"
CACHE_TTL = 60 * 30
CACHE_KIND = "pagev7"
_MAX_LIST = 200
_MEM_CACHE = {}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Referer": f"{BASE_URL}/",
})
from requests.adapters import HTTPAdapter
_pool = HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=1)
SESSION.mount("https://", _pool)
SESSION.mount("http://", _pool)

MAIN_CATEGORIES = [
    ("🎵 Müzik", f"{BASE_URL}/genre/muzik"),
    ("🎭 Radyo Tiyatrosu", f"{BASE_URL}/genre/radyo-tiyatrosu"),
    ("📚 Sesli Kitap", f"{BASE_URL}/genre/sesli-kitap"),
    ("🎙️ Podcast", f"{BASE_URL}/genre/podcast"),
    ("❤ Favoriler", "favorites:"),
]

SKIP_TITLES = {
    "trt dinle", "anasayfa", "giriş yap", "üye ol", "ara", "kapat",
    "canlı radyo", "frekanslar", "hakkımızda", "iletişim", "kvkk",
}
VALID_PATHS = ("/show/", "/playlist/", "/artist/", "/album/", "/genre/")

GROUP_LABELS = [
    ("genre", "📁 Kategoriler"),
    ("album", "💿 Albümler"),
    ("playlist", "🎵 Çalma Listeleri"),
    ("show", "📻 Programlar"),
    ("artist", "👤 Sanatçılar"),
    ("other", "• Diğer"),
]
SEG_ICON = {"playlist": "🎵", "album": "💿", "show": "📻", "artist": "👤", "genre": "📁"}

C_BG = "#0c0e14"
C_SURFACE = "#141820"
C_PRIMARY = "#e8a838"
C_ACCENT = "#38d9a9"
C_TEXT = "#e8e0d4"
C_MUTED = "#6b7280"
C_HEADER = "#1c1208"
C_TRACK = "#0f1318"

def normalize_url(url):
    url = (url or "").strip().strip("{}")
    if not url: return url
    if url.startswith("/"): return urljoin(BASE_URL, url)
    if not url.startswith(("http://", "https://")): url = "https://" + url
    p = urlparse(url)
    if p.netloc in ("trtdinle.com", "www.trtdinle.com"):
        return BASE_URL + p.path + (("?" + p.query) if p.query else "")
    return url

def clean_text(value):
    value = (value or "").replace("\\u002F", "/")
    if "\\u" in value:
        try: value = value.encode("utf-8").decode("unicode_escape")
        except Exception: pass
    return re.sub(r"\s+", " ", value).strip()

def clean_title(value, fallback="TRT Dinle"):
    value = clean_text(value or fallback)
    value = re.sub(r"\s*[|\-\u2013\u2014]\s*TRT D[\u0130i]nle.*$", "", value, flags=re.I).strip()
    return value or fallback

def norm_name(s):
    s = clean_text(s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def turkish_fold(s):
    if not s: return ""
    m = {
        "I": "ı", "İ": "i", "Ş": "ş", "Ç": "ç", "Ğ": "ğ", "Ö": "ö", "Ü": "ü",
        "Â": "a", "Î": "i", "Û": "u",
        "ı": "ı", "i": "i", "ş": "ş", "ç": "ç", "ğ": "ğ", "ö": "ö", "ü": "ü"
    }
    s = "".join(m.get(c, c) for c in str(s))
    return s.lower()

def _parse_dur(value):
    if not value: return 0
    s = str(value)
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 3: return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
            if len(parts) == 2: return int(parts[0])*60 + int(parts[1])
        except Exception: return 0
    try: return int(float(value))
    except Exception: return 0

def format_duration(seconds):
    try: seconds = int(float(seconds or 0))
    except Exception: seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def load_json(path, default):
    if path.exists():
        try: return json.loads(path.read_text(encoding="utf-8"))
        except Exception: return default
    return default

def save_json(path, data):
    try: path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception: pass

_HISTORY_CACHE = None
_FAVORITES_CACHE = None

def load_history():
    global _HISTORY_CACHE
    if _HISTORY_CACHE is None:
        _HISTORY_CACHE = load_json(HISTORY_FILE, [])
    return _HISTORY_CACHE

def add_to_history(url, title):
    url = normalize_url(url)
    history = [h for h in load_history() if h.get("url") != url]
    history.insert(0, {"url": url, "title": clean_title(title), "ts": time.time()})
    global _HISTORY_CACHE
    _HISTORY_CACHE = history[:50]
    save_json(HISTORY_FILE, _HISTORY_CACHE)

def load_favorites():
    global _FAVORITES_CACHE
    if _FAVORITES_CACHE is None:
        _FAVORITES_CACHE = load_json(FAVORITES_FILE, {})
    return _FAVORITES_CACHE

def save_favorites(data):
    global _FAVORITES_CACHE
    _FAVORITES_CACHE = data
    save_json(FAVORITES_FILE, data)

def is_favorite(ep):
    k = track_fav_key(ep) if isinstance(ep, dict) else norm_name(ep)
    return k in load_favorites()

def track_fav_key(ep):
    uid = ep.get("stream_url") or ep.get("download_url") or ep.get("url", "")
    if uid: return norm_name(uid)
    return norm_name(ep.get("url", "")) + "::" + (ep.get("title", "") or "")

def toggle_favorite(ep):
    favs = load_favorites()
    k = track_fav_key(ep)
    if k in favs:
        del favs[k]
        save_favorites(favs)
        return False
    favs[k] = {"url": ep.get("url",""), "stream_url": ep.get("stream_url",""),
               "title": ep.get("title",""), "artist": ep.get("artist",""),
               "duration": ep.get("duration",0)}
    save_favorites(favs)
    return True

def cache_get(url):
    norm = normalize_url(url)
    if norm in _MEM_CACHE:
        item = _MEM_CACHE[norm]
        if time.time() - item.get("ts", 0) < CACHE_TTL:
            return item.get("value")
    item = load_json(CACHE_FILE, {}).get(f"{CACHE_KIND}:{norm}")
    if item and time.time() - item.get("ts", 0) < CACHE_TTL:
        _MEM_CACHE[norm] = item
        return item.get("value")
    return None

def cache_set(url, value):
    norm = normalize_url(url)
    item = {"ts": time.time(), "value": value}
    _MEM_CACHE[norm] = item
    def write():
        cache = load_json(CACHE_FILE, {})
        cache[f"{CACHE_KIND}:{norm}"] = item
        if len(cache) > 120:
            cache = dict(sorted(cache.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)[:80])
        save_json(CACHE_FILE, cache)
    threading.Thread(target=write, daemon=True).start()

def fetch_soup(url):
    r = SESSION.get(normalize_url(url), timeout=(3, 8))
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")

def page_title(soup, fallback_url):
    og = soup.find("meta", property="og:title")
    if og and og.get("content"): return clean_title(og["content"])
    if soup.title and soup.title.string: return clean_title(soup.title.string)
    return clean_title(fallback_url.rstrip("/").split("/")[-1].replace("-", " "))

def page_cover(soup):
    og = soup.find("meta", property="og:image")
    if og and og.get("content"): return og["content"]
    return None

def ellipsize(text, width):
    text = clean_text(text)
    width = max(2, int(width))
    return text if len(text) <= width else text[:width-1] + "…"

def bar(pct, width, fill="━", empty="─"):
    pct = max(0.0, min(1.0, float(pct or 0)))
    width = max(8, int(width))
    n = round(width * pct)
    return fill * n + empty * (width - n)

def track_label(ep):
    a = ep.get("artist") or ""
    t = ep.get("title", "")
    return f"{t} — {a}" if a else t

def esc(text):
    return str(text).replace("[", r"\[")

def columns(parts):
    out = []
    for text, width, align in parts:
        text = clean_text(str(text))
        width = max(1, int(width))
        if len(text) > width:
            text = text[:width-1] + "…"
        out.append(format(text, f"{align}{width}"))
    return "".join(out)

def progress_line(pos, dur, width):
    width = max(10, int(width))
    pct = (pos / dur) if dur and dur > 0 else 0.0
    pct = max(0.0, min(1.0, pct))
    n = int(round((width - 1) * pct))
    left = "━" * n
    right = "─" * (width - 1 - n)
    return f"[{C_ACCENT}]{left}●{right}[/]"

def progress_bar(pos, dur, width):
    width = max(10, int(width))
    pct = (pos / dur) if dur and dur > 0 else 0.0
    pct = max(0.0, min(1.0, pct))
    n = int(round((width - 2) * pct))
    bar = "━" * n + "●" + "─" * (width - 2 - n)
    return f"[{C_ACCENT}]{bar}[/]"

VIS_BLOCKS = " ▁▂▃▄▅▆▇█"

def _lerp(a, b, t):
    return int(round(a + (b - a) * t))

def _vis_color(level):
    low = (0x21, 0x5a, 0x66)
    mid = (0x34, 0xc6, 0xd9)
    high = (0xf2, 0xb3, 0x5e)
    if level < 0.5:
        t = level / 0.5
        rgb = tuple(_lerp(low[i], mid[i], t) for i in range(3))
    else:
        t = (level - 0.5) / 0.5
        rgb = tuple(_lerp(mid[i], high[i], t) for i in range(3))
    return "#%02x%02x%02x" % rgb


# ================== NUXT 2 PARSER ==================

def split_args(src):
    parts, cur, depth = [], [], 0
    in_str, esc2, quote = False, False, ""
    for ch in src:
        if in_str:
            cur.append(ch)
            if esc2: esc2 = False
            elif ch == "\\": esc2 = True
            elif ch == quote: in_str = False
            continue
        if ch in ('"', "'"): in_str, quote = True, ch; cur.append(ch)
        elif ch in "([{": depth += 1; cur.append(ch)
        elif ch in ")]}": depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0: parts.append("".join(cur).strip()); cur = []
        else: cur.append(ch)
    if cur: parts.append("".join(cur).strip())
    return parts

def parse_nuxt(script):
    m = re.match(r"window\.__NUXT__=\(function\((.*?)\)\{", script)
    if not m: return {}, script
    params = [p.strip() for p in m.group(1).split(",")]
    body_start = script.find("{", script.find("function"))
    depth, in_str, esc2 = 0, False, False
    body_end = body_start
    for i in range(body_start, len(script)):
        ch = script[i]
        if in_str:
            if esc2: esc2 = False
            elif ch == "\\": esc2 = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0: body_end = i; break
    body = script[body_start:body_end + 1]
    tail = script[body_end + 1:]
    a, b = tail.find("("), tail.rfind(")")
    args = split_args(tail[a + 1:b]) if a >= 0 and b > a else []
    return {p: args[i] for i, p in enumerate(params) if i < len(args)}, body

def unquote(value, pm=None):
    if value is None: return ""
    value = value.strip()
    if pm and value in pm: value = pm[value].strip()
    if value in ("null", "undefined", "true", "false"): return ""
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    return clean_text(value)

def prop(obj, name, pm=None):
    m = re.search(
        rf'(?<![A-Za-z0-9_$]){re.escape(name)}\s*:\s*("(?:\\.|[^"])*"|\'(?:\\.|[^\'])*\'|[A-Za-z_$][\w$]*|-?\d+(?:\.\d+)?|null|true|false)',
        obj,
    )
    return unquote(m.group(1), pm) if m else ""

def iter_objects(source):
    stack = []
    in_str, esc2, quote = False, False, ""
    for i, ch in enumerate(source):
        if in_str:
            if esc2: esc2 = False
            elif ch == "\\": esc2 = True
            elif ch == quote: in_str = False
            continue
        if ch in ('"', "'"): in_str, quote = True, ch
        elif ch == "{": stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            yield source[start:i + 1]

def nuxt_script(soup):
    for s in soup.find_all("script"):
        t = s.string or s.get_text() or ""
        if "window.__NUXT__" in t: return t
    return ""

ARTIST_PATH_RE = re.compile(r"/artist/([^\s\"'/]+?)-(\d+)")

def build_artist_map(source, pm):
    amap = {}
    for obj in iter_objects(source):
        path = prop(obj, "path", pm) or prop(obj, "slug", pm) or prop(obj, "shareUrl", pm)
        if "/artist/" not in path: continue
        aid = prop(obj, "id", pm) or prop(obj, "uuid", pm)
        title = prop(obj, "title", pm) or prop(obj, "name", pm)
        if title and not title.startswith(("/", "http")):
            if aid: amap[str(aid)] = clean_title(title, "")
            mm = ARTIST_PATH_RE.search(path)
            if mm: amap[str(mm.group(2))] = clean_title(title, "")
    return amap

def prettify_slug(slug):
    slug = re.sub(r"-\d+$", "", slug or "").replace("-", " ").strip()
    return slug.title() if slug else ""

def artist_links(obj, amap):
    names = []
    for slug, aid in ARTIST_PATH_RE.findall(obj):
        nm = amap.get(str(aid)) or prettify_slug(f"{slug}-{aid}")
        if nm and nm not in names: names.append(nm)
    return ", ".join(names[:3])

def artists_from_array(obj, pm, amap=None):
    amap = amap or {}
    for token in ("artists:[", "artist:[", "mainArtists:[", "singers:[", "performers:[", "interpreters:["):
        idx = obj.find(token)
        if idx < 0: continue
        depth, end = 0, idx + len(token) - 1
        for j in range(idx + len(token) - 1, min(len(obj), idx + 6000)):
            c = obj[j]
            if c == "[": depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0: end = j; break
        block = obj[idx:end + 1]
        names = []
        for sub in iter_objects(block):
            nm = prop(sub, "title", pm) or prop(sub, "name", pm)
            if not nm or nm.startswith(("/", "http")):
                sid = prop(sub, "id", pm) or prop(sub, "uuid", pm)
                nm = amap.get(str(sid), "") if sid else ""
            if nm and nm not in names and not nm.startswith(("/", "http")):
                names.append(nm)
        if not names:
            lk = artist_links(block, amap)
            if lk: return lk
        if names: return ", ".join(names[:3])
    return ""

def artist_of(obj, pm, amap=None):
    amap = amap or {}
    multi = artists_from_array(obj, pm, amap)
    if multi: return multi
    for key in ("artistTitle", "artistName", "artistsName", "albumArtist", "mainArtist", "singer", "performer", "author", "presenter", "ownerTitle"):
        v = prop(obj, key, pm)
        if v and not v.startswith(("/", "http")): return amap.get(str(v), v)
    for token in ("artist:{", "mainArtist:{", "album:{"):
        idx = obj.find(token)
        if idx >= 0:
            part = obj[idx:idx + 2000]
            t = prop(part, "title", pm) or prop(part, "name", pm)
            if not t:
                sid = prop(part, "id", pm)
                t = amap.get(str(sid), "") if sid else ""
            if t and not t.startswith(("/", "http")): return t
    lk = artist_links(obj, amap)
    if lk: return lk
    return ""

def _audio_block(obj, pm):
    download = prop(obj, "downloadUrl", pm)
    duration = prop(obj, "duration", pm)
    stream = ""
    ai = obj.find("audio:")
    if ai >= 0:
        ap = obj[ai:ai + 1800]
        stream = prop(ap, "url", pm)
        download = download or prop(ap, "downloadUrl", pm)
        ad = prop(ap, "duration", pm)
        if ad: duration = ad
    stream = stream or prop(obj, "streamUrl", pm)
    stream = normalize_url(stream or download)
    download = normalize_url(download or stream)
    if not stream: return None
    if any(p in urlparse(stream).path for p in VALID_PATHS): stream = download
    if not (stream.startswith("http") and any(x in stream for x in ("m3u8", "m4a", "mp3", ".aac"))):
        return None
    if stream.endswith("master.m4a"): stream = stream.replace("master.m4a", "master.m3u8")
    return stream, download, _parse_dur(duration)

def extract_audio(soup):
    script = nuxt_script(soup)
    if not script: return []
    pm, body = parse_nuxt(script)
    source = body or script
    amap = build_artist_map(source, pm)
    tracks, seen = [], set()
    for obj in iter_objects(source):
        if "audio:" not in obj and "downloadUrl:" not in obj: continue
        title = prop(obj, "title", pm) or prop(obj, "name", pm)
        if not title: continue
        ab = _audio_block(obj, pm)
        if not ab: continue
        stream, download, dur = ab
        key = download or stream
        if key in seen: continue
        seen.add(key)
        artist = artist_of(obj, pm, amap)
        tracks.append({
            "title": clean_title(title),
            "artist": clean_title(artist, "") if artist else "",
            "duration": dur, "stream_url": stream, "download_url": download,
        })
    return tracks

def extract_listing(soup, current_url):
    items, seen = [], set()
    cur_path = urlparse(normalize_url(current_url)).path.rstrip("/")
    def add(title, href, typ=None):
        href = normalize_url(href)
        path = urlparse(href).path
        t = clean_title(title, "")
        if not t or norm_name(t) in SKIP_TITLES or len(t) < 2 or len(t) > 100: return
        if not href.startswith(BASE_URL) or not any(p in path for p in VALID_PATHS): return
        if path.rstrip("/") == cur_path or href in seen: return
        seen.add(href)
        seg = path.strip("/").split("/")[0]
        items.append({"title": t, "url": href, "type": typ or seg})
    script = nuxt_script(soup)
    if script:
        pm, body = parse_nuxt(script)
        source = body or script
        for obj in iter_objects(source):
            href = prop(obj, "path", pm) or prop(obj, "shareUrl", pm) or prop(obj, "slug", pm)
            if not href or not any(p in href for p in VALID_PATHS): continue
            title = prop(obj, "title", pm) or prop(obj, "name", pm)
            add(title, href, prop(obj, "type", pm))
    if len(items) < 3:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not any(p in urlparse(normalize_url(href)).path for p in VALID_PATHS): continue
            title = a.get("title") or a.get("aria-label") or a.get_text(" ", strip=True)
            add(title, href)
    return items

def group_items(items):
    buckets = {key: [] for key, _ in GROUP_LABELS}
    for it in items:
        seg = urlparse(it["url"]).path.strip("/").split("/")[0]
        buckets.get(seg, buckets["other"]).append(it)
    groups = []
    for key, label in GROUP_LABELS:
        if buckets[key]: groups.append({"key": key, "label": label, "items": buckets[key]})
    return groups

def filter_artist_tracks(tracks, page_title_str):
    base = norm_name(re.split(r"[\-\u2013\u2014|(]", page_title_str)[0])
    toks = {t for t in base.split() if len(t) > 1}
    if not toks: return tracks
    kept = []
    for tr in tracks:
        a = norm_name(tr.get("artist") or "")
        if not a: kept.append(tr); continue
        if toks & set(a.split()): kept.append(tr)
    return kept or tracks


# ================== NUXT 3 PARSER ==================

_AUDIO_HINTS = ("m3u8", "m4a", ".mp3", ".aac", ".ogg")
def _is_audio_url(u): return isinstance(u, str) and any(h in u for h in _AUDIO_HINTS)

def nuxt_data_array(soup):
    tag = soup.find("script", id="__NUXT_DATA__")
    if tag is None:
        for s in soup.find_all("script", attrs={"type": "application/json"}):
            txt = (s.string or s.get_text() or "").strip()
            if txt.startswith("["): tag = s; break
    if tag is None: return None
    txt = (tag.string or tag.get_text() or "").strip()
    try: data = json.loads(txt)
    except Exception: return None
    return data if isinstance(data, list) and data else None

def resolve_nuxt_data(soup):
    values = nuxt_data_array(soup)
    if values is None: return None
    cache = {}
    def res(i):
        if not isinstance(i, int): return i
        if i < 0 or i >= len(values): return None
        if i in cache: return cache[i]
        v = values[i]
        if isinstance(v, dict):
            out = {}; cache[i] = out
            for k, idx in v.items(): out[k] = res(idx) if isinstance(idx, int) else idx
            return out
        if isinstance(v, list):
            out = []; cache[i] = out
            for idx in v: out.append(res(idx) if isinstance(idx, int) else idx)
            return out
        cache[i] = v; return v
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(100000)
    try: return res(0)
    except RecursionError: return None
    finally: sys.setrecursionlimit(old_limit)

def _urls_from_dict(d):
    stream, download = "", ""
    for k in ("url", "streamUrl", "hls", "hlsUrl", "m3u8", "playUrl", "src"):
        v = d.get(k)
        if _is_audio_url(v): stream = v; break
    for k in ("downloadUrl", "download", "mp4", "m4a", "fileUrl"):
        v = d.get(k)
        if _is_audio_url(v): download = v; break
    raw = stream or download
    if not _is_audio_url(raw): return None
    stream = normalize_url(stream or download)
    download = normalize_url(download or stream)
    if stream.endswith("master.m4a"): stream = stream.replace("master.m4a", "master.m3u8")
    return stream, download

def audio_from_dict(d):
    for k in ("audio", "media", "stream", "sound", "streamInfo"):
        sub = d.get(k)
        if isinstance(sub, dict):
            r = _urls_from_dict(sub)
            if r: return r[0], r[1], _parse_dur(sub.get("duration") or d.get("duration"))
        elif _is_audio_url(sub):
            s = normalize_url(sub)
            if s.endswith("master.m4a"): s = s.replace("master.m4a", "master.m3u8")
            return s, s, _parse_dur(d.get("duration"))
    r = _urls_from_dict(d)
    if r: return r[0], r[1], _parse_dur(d.get("duration"))
    return None

def _names_from(value):
    names = []
    if isinstance(value, list):
        for x in value:
            if isinstance(x, dict):
                nm = x.get("title") or x.get("name") or x.get("fullName")
                if isinstance(nm, str) and nm.strip(): names.append(nm.strip())
            elif isinstance(x, str) and x.strip(): names.append(x.strip())
    elif isinstance(value, dict):
        nm = value.get("title") or value.get("name") or value.get("fullName")
        if isinstance(nm, str) and nm.strip(): names.append(nm.strip())
    elif isinstance(value, str) and value.strip(): names.append(value.strip())
    seen, out = set(), []
    for n in names:
        key = norm_name(n)
        if key and key not in seen: seen.add(key); out.append(n)
    return out

def artist_from_dict(d):
    if not isinstance(d, dict): return ""
    for k in ("artists", "mainArtists", "singers", "performers", "interpreters", "contributors", "artistList"):
        names = _names_from(d.get(k))
        if names: return ", ".join(names[:3])
    for k in ("artist", "mainArtist", "singer", "performer", "owner", "album"):
        names = _names_from(d.get(k))
        if names: return ", ".join(names[:3])
    for k in ("artistName", "artistTitle", "artistsName", "singerName", "ownerTitle", "albumArtist", "author", "presenter"):
        v = d.get(k)
        if isinstance(v, str) and v.strip(): return v.strip()
    return ""

def _str_field(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip(): return v
    return ""

def deep_collect(root, current_url):
    cur_path = urlparse(normalize_url(current_url)).path.rstrip("/")
    tracks, items = [], []
    seen_tracks, seen_items = set(), set()
    visited = set()
    stack = [(root, "")]
    while stack:
        node, parent_artist = stack.pop()
        if isinstance(node, dict):
            nid = id(node)
            if nid in visited: continue
            visited.add(nid)
            current_artist = artist_from_dict(node) or parent_artist
            title = _str_field(node, ("title", "name", "trackTitle"))
            au = audio_from_dict(node)
            if au and title:
                stream, download, dur = au
                key = download or stream
                if key not in seen_tracks:
                    seen_tracks.add(key)
                    tracks.append({
                        "title": clean_title(title),
                        "artist": clean_title(current_artist, "") or "",
                        "duration": dur, "stream_url": stream, "download_url": download,
                    })
            elif title:
                path = _str_field(node, ("path", "slug", "shareUrl", "link"))
                if path and any(p in path for p in VALID_PATHS):
                    href = normalize_url(path)
                    p = urlparse(href).path
                    if (href.startswith(BASE_URL) and p.rstrip("/") != cur_path and href not in seen_items):
                        seen_items.add(href)
                        seg = p.strip("/").split("/")[0]
                        items.append({"title": clean_title(title, ""), "url": href, "type": seg})
            for v in node.values():
                if isinstance(v, (dict, list)): stack.append((v, current_artist))
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, (dict, list)): stack.append((v, parent_artist))
    return tracks, items

def parse_page(soup, url):
    root = resolve_nuxt_data(soup)
    if root is not None:
        tracks, items = deep_collect(root, url)
        if tracks or items: return tracks, items
    return extract_audio(soup), extract_listing(soup, url)

def load_search_page(url, keyword):
    import requests
    api_url = f"https://www.trtdinle.com/api/search?keyword={requests.utils.quote(keyword)}&limit=100&type=all"
    r = SESSION.get(api_url, timeout=(3, 8))
    r.raise_for_status()
    data = r.json()

    tracks = []
    if "song" in data:
        for s in data["song"]:
            title = s.get("title", "")

            # Handle artist as list or dict
            artist_obj = s.get("artist") or []
            artist_name = ""
            if isinstance(artist_obj, list):
                artist_name = ", ".join(a.get("title", "") for a in artist_obj if isinstance(a, dict) and a.get("title"))
            elif isinstance(artist_obj, dict):
                artist_name = artist_obj.get("title") or ""
            elif isinstance(artist_obj, str):
                artist_name = artist_obj

            # Audio and duration
            audio_obj = s.get("audio") or {}
            stream_url = ""
            download_url = ""
            duration = 0
            if isinstance(audio_obj, dict):
                stream_url = audio_obj.get("url") or ""
                download_url = audio_obj.get("downloadUrl") or stream_url
                duration = audio_obj.get("duration") or 0

            if not stream_url:
                stream_url = s.get("streamUrl") or s.get("downloadUrl") or ""
                download_url = s.get("downloadUrl") or stream_url
                duration = s.get("duration") or 0

            stream_url = normalize_url(stream_url)
            download_url = normalize_url(download_url)

            tracks.append({
                "title": clean_title(title),
                "artist": clean_title(artist_name, "") if artist_name else "",
                "duration": _parse_dur(duration),
                "stream_url": stream_url,
                "download_url": download_url,
                "url": normalize_url(s.get("path") or ""),
                "cover": s.get("imageUrl") or s.get("featuredImage") or ""
            })

    groups = []
    group_mappings = [
        ("artist", "👤 Sanatçılar"),
        ("album", "💿 Albümler"),
        ("playlist", "🎵 Çalma Listeleri"),
        ("podcast", "🎙 Podcastler"),
        ("seslikitap", "📚 Sesli Kitaplar"),
        ("radyotiyatrosu", "📻 Radyo Tiyatrosu"),
        ("episode", "📻 Programlar/Bölümler")
    ]

    for api_key, label in group_mappings:
        items_list = data.get(api_key) or []
        if items_list:
            group_items = []
            for it in items_list:
                title = it.get("title") or ""
                path = it.get("path") or ""
                if title and path:
                    group_items.append({
                        "title": clean_title(title),
                        "url": normalize_url(path),
                        "type": api_key
                    })
            if group_items:
                groups.append({
                    "key": api_key,
                    "label": label,
                    "items": group_items
                })

    result = {
        "kind": "collection",
        "url": url,
        "title": f"Arama: {keyword}",
        "seg": "search",
        "tracks": tracks,
        "groups": groups,
        "cover": None
    }

    cache_set(url, result)
    return result

def load_page(url):
    url = normalize_url(url)
    cached = cache_get(url)
    if cached: return cached

    # Intercept search page queries
    p = urlparse(url)
    if "/ara" in p.path and "q=" in p.query:
        from urllib.parse import parse_qs
        qs = parse_qs(p.query)
        q_val = qs.get("q", [""])[0].strip()
        if q_val:
            return load_search_page(url, q_val)

    soup = fetch_soup(url)
    title = page_title(soup, url)
    cover = page_cover(soup)
    seg = urlparse(url).path.strip("/").split("/")[0]
    tracks, items = parse_page(soup, url)
    if seg == "genre": tracks = []
    if seg == "artist" and tracks: tracks = filter_artist_tracks(tracks, title)
    groups = group_items(items)
    if seg in ("playlist", "album") and tracks: groups = []
    if not tracks and not groups:
        raise ValueError("Bu sayfada gösterilecek içerik bulunamadı.")
    result = {"kind": "collection", "url": url, "title": title, "seg": seg,
              "tracks": tracks, "groups": groups, "cover": cover}
    for ep in tracks:
        if "url" not in ep: ep["url"] = url
        if "cover" not in ep: ep["cover"] = cover or ""
    cache_set(url, result)
    return result


# ================== PLAYER ENGINE ==================

class PlayerEngine:
    def __init__(self):
        self.player = None
        self.player_fallback = False
        self.queue = []
        self.idx = 0
        self.title = ""
        self.volume = 85
        self.playing = False
        self.manual_until = 0.0
        self.status = ""
        self.muted = False
        self.shuffle = False
        self.repeat = "off"
        self._rng = random.Random()
        self.sleep_until = 0.0
        self._pending_auto_next = False

    def ensure(self):
        if self.player is not None: return self.player
        if MPV_AVAILABLE and mpv is not None:
            try:
                self.player = mpv.MPV(
                    vo="null", video=False, ytdl=False,
                    hls_bitrate="max", cache="no",
                    log_handler=lambda *a: None,
                    msg_level="all=no",
                )
                self.player_fallback = False
            except Exception:
                self.player = MockMPV()
                self.player_fallback = True
        else:
            self.player = MockMPV()
            self.player_fallback = True

        try: self.player["volume"] = self.volume
        except Exception: pass

        @self.player.event_callback("end_file")
        def _ended(event):
            reason = str(getattr(event, "reason", "") or "").lower()
            if time.monotonic() < self.manual_until: return
            if reason and not any(ok in reason for ok in ("eof", "end")): return
            self.playing = False
            self._pending_auto_next = True
        return self.player

    def drain(self):
        if self._pending_auto_next:
            self._pending_auto_next = False
            try: self.next(auto=True)
            except Exception: pass

    def current(self):
        if 0 <= self.idx < len(self.queue): return self.queue[self.idx]
        return None

    def load(self, tracks, idx, title):
        self.queue = list(tracks or [])
        self.idx = max(0, min(idx, len(self.queue) - 1)) if self.queue else 0
        self.title = title
        def bg_play():
            try: self.play_current(manual=True)
            except Exception: pass
        threading.Thread(target=bg_play, daemon=True).start()

    def play_current(self, manual=False):
        p = self.ensure()
        ep = self.current()
        if not ep: return
        if manual: self.manual_until = time.monotonic() + 1.3
        for target in [ep.get("stream_url"), ep.get("download_url")]:
            if not target: continue
            try:
                p.command("loadfile", target, "replace")
                p.pause = False
                self.playing = True
                self.status = "Çalıyor"; return
            except Exception as e:
                self.status = f"Hata: {e}"
        self.playing = False

    def toggle_play(self):
        p = self.ensure()
        p.pause = not bool(p.pause)
        self.playing = not bool(p.pause)
        self.status = "Çalıyor" if self.playing else "Duraklatıldı"

    def next(self, auto=False):
        n = len(self.queue)
        if n == 0: return
        if auto and self.repeat == "one":
            self.play_current(manual=False); return
        if self.shuffle and n > 1:
            nxt = self.idx
            while nxt == self.idx: nxt = self._rng.randrange(n)
            self.idx = nxt
            self.play_current(manual=not auto); return
        if self.idx < n - 1:
            self.idx += 1; self.play_current(manual=not auto)
        elif self.repeat == "all":
            self.idx = 0; self.play_current(manual=not auto)
        else:
            self.playing = False
            self.status = "Liste tamamlandı"

    def prev(self):
        if self.idx > 0: self.idx -= 1
        self.play_current(manual=True)

    def jump(self, idx):
        if 0 <= idx < len(self.queue):
            self.idx = idx; self.play_current(manual=True)

    def remove_from_queue(self, idx):
        if not self.queue or idx < 0 or idx >= len(self.queue): return
        if idx == self.idx:
            if len(self.queue) == 1:
                self.queue = []; self.idx = 0; self.playing = False; self.status = ""
                try: self.player.command("stop")
                except Exception: pass
                return
            self.queue.pop(idx)
            if self.idx >= len(self.queue): self.idx = len(self.queue) - 1
            self.play_current(manual=True)
        else:
            self.queue.pop(idx)
            if idx < self.idx: self.idx -= 1

    def clear_queue(self):
        self.queue = []; self.idx = 0; self.playing = False; self.status = ""
        try: self.player.command("stop")
        except Exception: pass

    def seek(self, secs):
        if self.player:
            try: self.player.seek(secs, reference="relative")
            except Exception: pass

    def set_volume(self, v):
        self.volume = max(0, min(100, v))
        self.muted = False
        if self.player:
            try: self.player.mute = False; self.player.volume = self.volume
            except Exception: pass

    def toggle_mute(self):
        self.muted = not self.muted
        if self.player:
            try: self.player.mute = self.muted
            except Exception: pass

    def cycle_repeat(self):
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        return self.repeat

    def toggle_shuffle(self):
        self.shuffle = not self.shuffle
        return self.shuffle

    def position(self):
        if not self.player: return 0.0, 0.0
        try: pos = float(self.player.playback_time or 0)
        except Exception: pos = 0.0
        try: dur = float(self.player.duration or 0)
        except Exception: dur = 0.0
        if dur <= 0:
            ep = self.current()
            dur = float(ep.get("duration", 0)) if ep else 0.0
        return pos, dur

    def is_paused(self):
        if not self.player: return True
        try: return bool(self.player.pause)
        except Exception: return True

    def terminate(self):
        if self.player:
            try:
                self.manual_until = time.monotonic() + 2
                self.player.terminate()
            except Exception: pass
            self.player = None

    @property
    def sleep_remaining(self):
        if self.sleep_until <= 0: return 0
        return max(0, self.sleep_until - time.time())

    def set_sleep(self, minutes):
        if minutes <= 0: self.sleep_until = 0
        else: self.sleep_until = time.time() + minutes * 60


# ================== CUSTOM WIDGETS ==================

class Spectrum(Static):
    def __init__(self, bars=53, height=5, **kw):
        super().__init__("", **kw)
        self._n = max(8, int(bars))
        self._levels = [0.0] * self._n
        self._rng = random.Random()
        self._phase = 0.0
        self._h = max(1, height)

    def on_mount(self):
        self.set_interval(1 / 15, self._tick)

    def _active(self):
        try:
            eng = self.app.engine
        except Exception: return False
        return bool(eng.current()) and eng.playing and not eng.is_paused()

    def _tick(self):
        n = self._n
        active = self._active()
        self._phase += 0.22
        for i in range(n):
            center = 1.0 - abs(i - (n - 1) / 2) / (((n - 1) / 2) or 1)
            if active:
                wob = 0.5 + 0.5 * math.sin(self._phase * 1.3 + i * 0.45)
                wob2 = 0.5 + 0.5 * math.sin(self._phase * 0.7 - i * 0.21)
                noise = self._rng.random()
                tgt = (0.16 + 0.84 * center) * (0.30 + 0.70 * ((wob + wob2) / 2)) * (0.55 + 0.45 * noise)
                tgt = max(0.05, min(1.0, tgt))
            else: tgt = 0.0
            cur = self._levels[i]
            rate = 0.55 if tgt > cur else 0.30
            self._levels[i] = cur + (tgt - cur) * rate
        try: self.update(self._markup())
        except Exception: pass

    def _markup(self):
        rows = []
        for r in range(self._h, 0, -1):
            chunks = []
            i = 0
            while i < self._n:
                # Determine char and color for column i
                val = self._levels[i] * self._h
                active = self._active()
                if val >= r:
                    ch = "█"
                    color = _vis_color(self._levels[i]) if active else C_MUTED
                elif val > r - 1:
                    frac = val - (r - 1)
                    idx = int(frac * 8)
                    ch = VIS_BLOCKS[max(0, min(7, idx))]
                    color = _vis_color(self._levels[i]) if active else C_MUTED
                else:
                    ch = " "
                    color = C_TRACK

                # Find run of same char and color
                j = i + 1
                while j < self._n:
                    val_j = self._levels[j] * self._h
                    if val_j >= r:
                        ch_j = "█"
                        color_j = _vis_color(self._levels[j]) if active else C_MUTED
                    elif val_j > r - 1:
                        frac_j = val_j - (r - 1)
                        idx_j = int(frac_j * 8)
                        ch_j = VIS_BLOCKS[max(0, min(7, idx_j))]
                        color_j = _vis_color(self._levels[j]) if active else C_MUTED
                    else:
                        ch_j = " "
                        color_j = C_TRACK

                    if ch_j == ch and color_j == color:
                        j += 1
                    else:
                        break

                chunks.append(f"[{color}]{ch * (j - i)}[/]")
                i = j
            rows.append("".join(chunks))
        return "\n".join(rows)


# ================== NOWBAR MIXIN ==================

NOWBAR_CSS = f"""
    #nowbar {{ dock: bottom; height: 1; background: {C_HEADER}; color: {C_ACCENT}; padding: 0 2; text-style: bold; }}
    #nowbar.empty {{ display: none; }}
"""

class NowBarMixin:
    def mount_nowbar_timer(self):
        self.set_interval(1.0, self.refresh_nowbar)
        self.refresh_nowbar()

    def refresh_nowbar(self):
        try: bar_w = self.query_one("#nowbar", Static)
        except Exception: return
        eng = self.app.engine
        eng.drain()
        ep = eng.current()
        if not ep:
            bar_w.add_class("empty")
            return
        bar_w.remove_class("empty")
        pos, dur = eng.position()
        playing = eng.playing and not eng.is_paused()
        state = f"[{C_PRIMARY}]▶[/]" if playing else f"[{C_MUTED}]⏸[/]"
        mini = progress_line(pos, dur, 18)
        times = f"{format_duration(pos) or '0:00'} / {format_duration(dur) or '?'}"
        sleep_str = ""
        if eng.sleep_remaining > 0:
            mins = int(eng.sleep_remaining / 60)
            sleep_str = f"  ⏰ {mins}dk"
        bar_w.update(
            f"{state}  [b]{esc(ellipsize(track_label(ep), 44))}[/]   {mini}  {times}   ·   p: oynatıcı{sleep_str}"
        )

    def action_open_player(self):
        if self.app.engine.current():
            self.app.push_screen(PlayerScreen())
        else:
            self.app.notify("Önce bir parça başlat.", timeout=3)


# ================== LOADING SCREEN ==================

class LoadingDone(Message):
    def __init__(self, data):
        super().__init__()
        self.data = data

class LoadingFailed(Message):
    def __init__(self, error):
        super().__init__()
        self.error = error

class LoadingScreen(Screen):
    CSS = f"""
    LoadingScreen {{ align: center middle; background: {C_BG}; }}
    #load-box {{ width: 56; height: 9; padding: 1 2; border: round {C_PRIMARY}; content-align: center middle; background: {C_SURFACE}; }}
    #load-msg {{ text-align: center; color: {C_PRIMARY}; margin-bottom: 1; }}
    """

    def __init__(self, url, **kw):
        super().__init__(**kw)
        self.url = normalize_url(url)

    def compose(self):
        with Container(id="load-box"):
            yield Static("⏳ Yükleniyor...", id="load-msg")
            yield LoadingIndicator()

    def on_mount(self):
        self.set_timer(0.1, self._fetch)

    @work(thread=True)
    def _fetch(self):
        try:
            data = load_page(self.url)
            add_to_history(self.url, data["title"])
            self.post_message(LoadingDone(data))
        except Exception as e:
            self.post_message(LoadingFailed(str(e)))

    def on_loading_done(self, ev):
        self.app.pop_screen()
        self.app.push_screen(CollectionScreen(ev.data))

    def on_loading_failed(self, ev):
        self.app.pop_screen()
        self.app.notify(f"Açılamadı: {ev.error}", severity="error", timeout=6)


# ================== HOME SCREEN ==================

class HomeScreen(NowBarMixin, Screen):
    CSS = f"""
    HomeScreen {{ align: center middle; background: {C_BG}; }}
    #home-box {{ width: 120; max-width: 98%; height: auto; max-height: 96%; padding: 2 4; border: round {C_ACCENT}; background: {C_SURFACE}; overflow-y: auto; }}
    #home-logo {{ text-align: center; text-style: bold; color: {C_ACCENT}; padding: 1 0; }}
    #home-sub {{ text-align: center; color: {C_MUTED}; margin-bottom: 2; }}
    .sec {{ text-style: bold; color: {C_PRIMARY}; margin-top: 1; }}
    #cat-list, #hist-list {{ border: solid {C_PRIMARY}; background: {C_BG}; }}
    #cat-list {{ height: auto; max-height: 10; }}
    #hist-list {{ height: auto; max-height: 12; }}
    ListView:focus > ListItem.--highlight {{ background: {C_PRIMARY}; color: {C_BG}; text-style: bold; }}
    ListView > ListItem.--highlight {{ background: {C_TRACK}; }}
    """ + NOWBAR_CSS

    BINDINGS = [
        Binding("escape", "quit", "Çıkış"),
        Binding("left", "quit", "Çıkış", show=False),
        Binding("right", "select_focused", "Seç", show=False),
        Binding("ctrl+r", "clear_cache", "Cache temizle"),
        Binding("ctrl+l", "clear_history", "Geçmiş temizle"),
        Binding("p", "open_player", "Oynatıcı"),
    ]

    def compose(self):
        yield Header(show_clock=True)
        with Container(id="home-box"):
            yield Static("""
┏━━━━┓ ┏━━━┓ ┏━━━━┓    ┏━━━┓ ┏┓ ┏━┓ ┏┓ ┏┓     ┏━━━━┓
┃┏┓┏┓┃ ┃┏━┓┃ ┃┏┓┏┓┃    ┃┏━┓┃ ┃┃ ┃ ┗┓┃┃ ┃┃     ┃┏━━━┛
┗┛┃┃┗┛ ┃┗━┛┃ ┗┛┃┃┗┛    ┃┃ ┃┃ ┃┃ ┃┏┓┗┛┃ ┃┃     ┃┗━━┓
  ┃┃   ┃┏┓┏┛   ┃┃      ┃┃ ┃┃ ┃┃ ┃┃┗┓ ┃ ┃┃     ┃┏━━┛
  ┃┃   ┃┃┃┗┓   ┃┃      ┃┗━┛┃ ┃┃ ┃┃ ┃ ┃ ┃┗━━━┓ ┃┗━━━┓
  ┗┛   ┗┛┗━┛   ┗┛      ┗━━━┛ ┗┛ ┗┛ ┗━┛ ┗━━━━┛ ┗━━━━┛
""", id="home-logo")
            yield Static("TRT Dinle  ·  Müzik • Tiyatro • Kitap • Podcast", id="home-sub")
            yield Static("📂 Kategoriler", classes="sec")
            yield ListView(id="cat-list")
            yield Static("🔗 URL Aç", classes="sec")
            yield Input(placeholder="trtdinle.com link yapıştır", id="url-input")
            yield Static("🕐 Geçmiş", classes="sec")
            yield ListView(id="hist-list")
        yield Static("", id="nowbar", classes="empty")
        yield Footer()

    def on_mount(self):
        lv = self.query_one("#cat-list", ListView)
        for title, url in MAIN_CATEGORIES:
            lv.append(ListItem(Label(f"  {title}"), name=url))
        lv.index = 0
        self._refresh_history()
        self.mount_nowbar_timer()

    def _refresh_history(self):
        lv = self.query_one("#hist-list", ListView)
        lv.clear()
        for item in load_history():
            ts = item.get("ts") or 0
            label = ellipsize(item.get("title", ""), 65)
            if ts:
                try:
                    age = time.time() - ts
                    if age < 3600: time_str = f"{int(age/60)}dk"
                    elif age < 86400: time_str = f"{int(age/3600)}s"
                    else: time_str = f"{int(age/86400)}g"
                    label = f"{time_str:>4}  {label}"
                except Exception: pass
            lv.append(ListItem(Label(f"  {label}"), name=item.get("url")))

    def _open(self, url):
        url = url.strip()
        if url == "favorites:":
            favs = load_favorites()
            items = [v for v in favs.values()]
            self.app.push_screen(CollectionScreen({"title": "❤ Favoriler", "tracks": items, "groups": []}))
            return
        # Auto search term translator
        if not url.startswith(("http://", "https://", "favorites:")) and not url.startswith("/") and len(url) > 0:
            url = f"https://www.trtdinle.com/ara?q={url}"
        url = normalize_url(url)
        if url: self.app.push_screen(LoadingScreen(url))

    @on(Input.Submitted, "#url-input")
    def _url(self, e): self._open(e.value)

    @on(ListView.Selected, "#cat-list")
    def _cat(self, e): self._open(e.item.name)

    @on(ListView.Selected, "#hist-list")
    def _hist(self, e): self._open(e.item.name)

    def action_clear_cache(self):
        save_json(CACHE_FILE, {})
        self.app.notify("Cache temizlendi")

    def action_clear_history(self):
        save_json(HISTORY_FILE, [])
        self._refresh_history()
        self.app.notify("Geçmiş temizlendi")

    def action_select_focused(self):
        from textual.widgets import ListView
        f = self.focused
        if isinstance(f, ListView): f.action_select_cursor()


# ================== COLLECTION SCREEN ==================

class CollectionScreen(NowBarMixin, Screen):
    CSS = f"""
    CollectionScreen {{ layout: vertical; background: {C_BG}; }}
    #col-head {{ height: auto; padding: 1 2; background: {C_SURFACE}; border-bottom: solid {C_ACCENT}; }}
    #col-title {{ text-style: bold; color: {C_TEXT}; }}
    #col-count {{ color: {C_MUTED}; }}
    #col-search {{ margin: 1 2 0 2; border: solid {C_ACCENT}; background: {C_BG}; }}
    #col-list {{ height: 1fr; margin: 1 2; border: solid {C_PRIMARY}; background: {C_BG}; }}
    #col-list .hdr {{ color: {C_PRIMARY}; text-style: bold; background: {C_TRACK}; }}
    #col-list .playall {{ color: {C_ACCENT}; text-style: bold; }}
    #col-list:focus > ListItem.--highlight {{ background: {C_PRIMARY}; color: {C_BG}; text-style: bold; }}
    #col-list > ListItem.--highlight {{ background: {C_TRACK}; }}
    """ + NOWBAR_CSS

    BINDINGS = [
        Binding("escape,q", "back", "Geri"),
        Binding("left", "back", "Geri", show=False),
        Binding("right", "select_focused", "Seç", show=False),
        Binding("slash", "focus_search", "Ara"),
        Binding("p", "open_player", "Oynatıcı"),
        Binding("f", "toggle_favorite", "Favori"),
    ]

    def __init__(self, data, **kw):
        super().__init__(**kw)
        self.data = data
        self.title_text = data.get("title", "TRT Dinle")
        self.tracks = data.get("tracks", [])
        self.groups = data.get("groups", [])

    def compose(self):
        yield Header(show_clock=False)
        with Container(id="col-head"):
            yield Static(self.title_text, id="col-title")
            yield Static("", id="col-count")
        yield Input(placeholder="🔍 Ara...", id="col-search")
        yield ListView(id="col-list")
        yield Static("", id="nowbar", classes="empty")
        yield Footer()

    def on_mount(self):
        self._populate("")
        self.mount_nowbar_timer()
        self.query_one("#col-list", ListView).focus()

    def _hdr(self, text):
        return ListItem(Label(text), classes="hdr", disabled=True)

    def _populate(self, query):
        q = query.lower().strip()
        lv = self.query_one("#col-list", ListView)
        lv.clear()
        total = 0
        width = max(46, (self.size.width or 80) - 10)
        num_w, dur_w = 4, 7
        rest = max(20, width - num_w - dur_w - 2)
        title_w = max(12, int(rest * 0.60))
        artist_w = max(8, rest - title_w)

        tracks = [(i, ep) for i, ep in enumerate(self.tracks) if not q or q in turkish_fold(track_label(ep))]
        if tracks:
            lv.append(self._hdr(f"  ♫  Parçalar · {len(self.tracks)}"))
            if not q:
                lv.append(ListItem(Label("    ▸  Hepsini çal"), name="all", classes="playall"))
            for idx, ep in tracks[:_MAX_LIST]:
                dur = format_duration(ep.get("duration"))
                row = columns([
                    (f"{idx+1}", num_w, ">"),
                    (" ", 1, "<"),
                    (ep.get("title", ""), title_w, "<"),
                    (" ", 1, "<"),
                    (ep.get("artist", "") or "—", artist_w, "<"),
                    (dur, dur_w, ">"),
                ])
                lv.append(ListItem(Label(esc(" " + row)), name=f"t:{idx}"))
                total += 1

        for g in self.groups:
            gi = [it for it in g["items"] if not q or q in turkish_fold(it["title"])]
            if not gi: continue
            lv.append(self._hdr(f"  {g['label']} · {len(g['items'])}"))
            for it in gi[:_MAX_LIST]:
                seg = urlparse(it["url"]).path.strip("/").split("/")[0]
                icon = SEG_ICON.get(seg, "•")
                lv.append(ListItem(Label(esc(f"   {icon}  {ellipsize(it['title'], width-8)}")), name=f"u:{it['url']}"))
                total += 1

        if total == 0:
            lv.append(self._hdr("  Sonuç yok"))
        pieces = []
        if self.tracks:
            pieces.append(f"{len(self.tracks)} parça")
        gc = sum(len(g['items']) for g in self.groups)
        if gc:
            pieces.append(f"{gc} koleksiyon")
        self.query_one("#col-count", Static).update(" · ".join(pieces) if pieces else "")

    @on(Input.Changed, "#col-search")
    def _search(self, e): self._populate(e.value)

    @on(ListView.Selected, "#col-list")
    def _select(self, e):
        name = e.item.name if e.item else None
        if not name: return
        if name == "all":
            self._play(0)
        elif name.startswith("t:"):
            self._play(int(name[2:]))
        elif name.startswith("u:"):
            self.app.push_screen(LoadingScreen(name[2:]))

    def _play(self, idx):
        self.app.engine.load(self.tracks, idx, self.title_text)
        self.app.push_screen(PlayerScreen())

    def action_back(self): self.app.pop_screen()

    def action_focus_search(self):
        self.query_one("#col-search", Input).focus()

    def action_select_focused(self):
        from textual.widgets import ListView
        f = self.focused
        if isinstance(f, ListView): f.action_select_cursor()

    def action_toggle_favorite(self):
        lv = self.query_one("#col-list", ListView)
        if lv.index is None: return
        children = list(lv.children)
        if lv.index >= len(children): return
        item = children[lv.index]
        if not item.name: return
        name = item.name
        if not name.startswith("t:"): return
        idx = int(name[2:])
        if not (0 <= idx < len(self.tracks)): return
        added = toggle_favorite(self.tracks[idx])
        self._populate(self.query_one("#col-search", Input).value)
        self.app.notify(
            f"{'❤ Favorilere eklendi' if added else '♡ Favorilerden çıkarıldı'}",
            timeout=2,
        )


# ================== SLEEP TIMER SCREEN ==================

class SleepTimerScreen(Screen):
    CSS = f"""
    SleepTimerScreen {{ align: center middle; background: rgba(0,0,0,0.7); }}
    #st-box {{ width: 46; height: auto; padding: 1 2; border: round {C_ACCENT}; background: {C_SURFACE}; }}
    #st-title {{ text-align: center; text-style: bold; color: {C_ACCENT}; margin-bottom: 1; }}
    #st-list {{ border: solid {C_PRIMARY}; background: {C_BG}; }}
    #st-list:focus > ListItem.--highlight {{ background: {C_PRIMARY}; color: {C_BG}; text-style: bold; }}
    #st-list > ListItem.--highlight {{ background: {C_TRACK}; }}
    """

    def __init__(self, active, **kw):
        super().__init__(**kw)
        self._active = active

    def compose(self):
        with Container(id="st-box"):
            yield Static("⏰ Uyku Zamanlayıcı", id="st-title")
            yield ListView(id="st-list")

    def on_mount(self):
        lv = self.query_one("#st-list", ListView)
        if self._active:
            lv.append(ListItem(Label("  ✕ İptal et"), name="0"))
        for m, label in [(5, "5 dk"), (15, "15 dk"), (30, "30 dk"), (60, "60 dk")]:
            lv.append(ListItem(Label(f"  {label}"), name=str(m)))
        lv.index = 1 if self._active else 0

    @on(ListView.Selected, "#st-list")
    def _sel(self, e):
        if e.item and e.item.name:
            self.dismiss(int(e.item.name))


# ================== PLAYER SCREEN ==================

class HelpModalScreen(Screen):
    CSS = f"""
    HelpModalScreen {{ align: center middle; background: rgba(0,0,0,0.75); }}
    #help-box {{ width: 66; height: auto; padding: 1 3; border: double {C_PRIMARY}; background: {C_SURFACE}; }}
    #help-title {{ text-align: center; text-style: bold; color: {C_PRIMARY}; margin-bottom: 1; }}
    #help-grid {{ layout: grid; grid-size: 2; grid-gutter: 1; margin: 1 0; }}
    .help-key {{ text-style: bold; color: {C_ACCENT}; text-align: right; margin-right: 2; }}
    .help-desc {{ color: {C_TEXT}; }}
    #help-close {{ margin-top: 1; align-horizontal: center; background: {C_SURFACE}; color: {C_TEXT}; border: tall {C_MUTED}; }}
    #help-close:focus {{ background: {C_PRIMARY}; color: {C_BG}; border: none; }}
    """

    def compose(self):
        with Container(id="help-box"):
            yield Static("⌨ Klavye Kısayolları", id="help-title")
            with Container(id="help-grid"):
                keys = [
                    ("Boşluk (Space)", "Oynat / Duraklat"),
                    ("← / →", "10 Saniye Geri / İleri"),
                    ("↑ / ↓", "Sesi Artır / Azalt"),
                    ("n", "Sonraki Parça"),
                    ("b", "Önceki Parça"),
                    ("l", "Çalma Listesini Göster/Gizle"),
                    ("s", "Karışık Çalmayı Aç/Kapat"),
                    ("r", "Tekrar Modunu Değiştir"),
                    ("m", "Sesi Kapa/Aç (Mute)"),
                    ("t", "Uyku Zamanlayıcısı"),
                    ("f", "Favorilere Ekle/Çıkar"),
                    ("d", "Parçayı Listeden Kaldır"),
                    ("/", "Listede Ara / Filtrele"),
                    ("Esc / q", "Önceki Sayfaya Dön"),
                    ("?", "Bu Yardım Menüsünü Aç"),
                ]
                for key, desc in keys:
                    yield Label(f" {key}", classes="help-key")
                    yield Label(desc, classes="help-desc")
            yield Button("Kapat", id="help-close")

    @on(Button.Pressed, "#help-close")
    def _close(self):
        self.dismiss()

class PlayerScreen(Screen):
    CSS = f"""
    PlayerScreen {{ layout: vertical; background: {C_BG}; }}
    #pl-head {{ height: auto; padding: 0 2; background: {C_HEADER}; border-bottom: solid {C_ACCENT}; }}
    #pl-track {{ text-style: bold; color: {C_TEXT}; padding: 0 0 0 0; }}
    #pl-meta {{ color: {C_MUTED}; padding: 0 0 0 0; }}
    #pl-main {{ layout: horizontal; height: 1fr; }}
    #pl-center {{ width: 1fr; padding: 1 2; }}
    #pl-stage {{ height: 1fr; min-height: 8; border: round {C_ACCENT}; background: {C_TRACK}; padding: 1; content-align: center middle; }}
    #pl-big-title {{ width: 100%; text-align: center; text-style: bold; color: {C_TEXT}; }}
    #pl-big-artist {{ width: 100%; text-align: center; color: {C_ACCENT}; }}
    #pl-vis {{ width: 100%; height: 3; text-align: center; margin: 1 0; }}
    #pl-bar {{ width: 100%; text-align: center; margin-top: 1; }}
    #pl-bar-t {{ width: 100%; text-align: center; color: {C_MUTED}; }}
    #pl-vol {{ width: 100%; text-align: center; color: {C_ACCENT}; margin-top: 1; }}
    #pl-controls {{ width: 100%; height: 3; align-horizontal: center; margin-top: 1; }}
    #pl-controls Button {{ height: 3; min-width: 6; border: none; background: {C_SURFACE}; color: {C_TEXT}; }}
    #pl-controls Button:hover {{ background: {C_PRIMARY}; color: {C_BG}; }}
    #pl-controls #pb-play {{ background: {C_PRIMARY}; color: {C_BG}; text-style: bold; margin: 0 1; }}
    #pl-modes {{ width: 100%; text-align: center; color: {C_MUTED}; margin-top: 1; }}
    #pl-status {{ width: 100%; text-align: center; color: {C_PRIMARY}; }}
    #pl-side {{ width: 74; border-left: solid {C_ACCENT}; background: {C_TRACK}; }}
    #pl-side.hidden {{ display: none; }}
    #pl-side-title {{ height: 3; padding: 1; text-align: center; text-style: bold; color: {C_PRIMARY}; background: {C_HEADER}; }}
    #pl-side-search {{ margin: 0 1; border: solid {C_MUTED}; background: {C_BG}; }}
    #pl-side-list {{ height: 1fr; }}
    #pl-side-list:focus > ListItem.--highlight {{ background: {C_PRIMARY}; text-style: bold; }}
    #pl-side-list > ListItem.--highlight {{ background: {C_TRACK}; }}
    #pl-foot {{ dock: bottom; height: 1; text-align: center; color: {C_MUTED}; background: {C_HEADER}; }}
    """

    BINDINGS = [
        Binding("space", "toggle_play", "Oynat/Duraklat"),
        Binding("right", "seek_fwd", "İleri"),
        Binding("left", "seek_back", "Geri"),
        Binding("plus,equals_sign,equal,kp_plus", "volume_up", "Ses +"),
        Binding("minus,underscore,kp_minus", "volume_down", "Ses -"),
        Binding("n", "next_track", "Sonraki"),
        Binding("b", "prev_track", "Önceki"),
        Binding("l", "toggle_list", "Liste"),
        Binding("s", "toggle_shuffle", "Karışık"),
        Binding("r", "cycle_repeat", "Tekrar"),
        Binding("m", "toggle_mute", "Sessiz"),
        Binding("t", "sleep_menu", "Uyku"),
        Binding("f", "toggle_favorite", "Favori"),
        Binding("d", "remove_track", "Kaldır"),
        Binding("slash", "focus_side_search", "Ara"),
        Binding("question_mark", "show_help", "Yardım"),
        Binding("escape,q", "back", "Geri"),
    ]

    def compose(self):
        yield Header(show_clock=False)
        with Container(id="pl-head"):
            yield Static("", id="pl-track")
            yield Static("", id="pl-meta")
        with Container(id="pl-main"):
            with Vertical(id="pl-center"):
                with Vertical(id="pl-stage"):
                    yield Static("", id="pl-big-title")
                    yield Static("", id="pl-big-artist")
                    yield Spectrum(id="pl-vis")
                    yield Static("", id="pl-bar")
                    yield Static("", id="pl-bar-t")
                yield Static("", id="pl-vol")
                with Horizontal(id="pl-controls"):
                    yield Button("◀◀", id="pb-prev")
                    yield Button("−10", id="pb-back")
                    yield Button("⏸", id="pb-play", variant="primary")
                    yield Button("+10", id="pb-fwd")
                    yield Button("▶▶", id="pb-next")
                yield Static("", id="pl-modes")
                yield Static("", id="pl-status")
            with Container(id="pl-side"):
                yield Static("TRT DINLE", id="pl-side-title")
                yield Input(placeholder="/ ile filtrele...", id="pl-side-search")
                yield ListView(id="pl-side-list")
        yield Static("space oynat · ↑/↓ ses · →← sar · n/b parça · l liste · d kaldır · f favori · ? yardım · q geri", id="pl-foot")

    def __init__(self, **kw):
        super().__init__(**kw)
        self._last_idx = -1
        self._w_all = {}
        self._side_query = ""
        self._side_populated = False

    def on_mount(self):
        for sid in ("pl-track", "pl-meta", "pl-big-title", "pl-big-artist",
                    "pl-bar", "pl-bar-t", "pl-vol",
                    "pl-modes", "pl-status", "pl-side-list", "pl-side",
                    "pl-stage", "pb-play", "pl-center", "pl-side-search",
                    "pl-side-title"):
            try: self._w_all[sid] = self.query_one(f"#{sid}")
            except Exception: pass
        self._render_header()
        self._update_vol()
        self._update_modes()
        self.set_interval(1.0, self._tick)
        self._populate_side()
        if not self._side_populated:
            self.set_timer(0.1, self._populate_side)
        self.set_timer(0.15, self._auto_show_side)

    def _auto_show_side(self):
        if not self.app.engine.queue: return
        side = self._w("pl-side")
        if not side: return
        side.remove_class("hidden")
        self._populate_side()
        try: self._w("pl-side-list").focus()
        except Exception: pass

    def _w(self, sid):
        return self._w_all.get(sid)

    def _cw(self, reserve=6):
        w = self._w("pl-center")
        try: cw = w.size.width - reserve
        except Exception: cw = 50
        return max(20, min(110, cw))

    def _render_header(self):
        eng = self.app.engine
        ep = eng.current()
        if not ep: return
        fav = is_favorite(ep)
        heart = f"[{C_PRIMARY}]♥[/] " if fav else ""
        self._upd("pl-track", f"{heart}[{C_ACCENT}]♪[/]  [b]{esc(ellipsize(track_label(ep), 65))}[/]")
        self._upd("pl-meta",
            f"[{C_MUTED}]{esc(ellipsize(eng.title, 40))}   {eng.idx+1}/{len(eng.queue)}[/]")
        cw = self._cw()
        self._upd("pl-big-title", esc(ellipsize(ep.get("title", ""), cw)))
        self._upd("pl-big-artist", esc(ellipsize(ep.get("artist", ""), cw)) if ep.get("artist") else "—")
        self._last_idx = eng.idx
        self._set_play_icon()

    def _upd(self, sid, val):
        w = self._w(sid)
        if w: w.update(val)

    def _set_play_icon(self):
        try:
            playing = self.app.engine.playing and not self.app.engine.is_paused()
            self._w("pb-play").label = "⏸" if playing else "▶"
        except Exception: pass

    def _populate_side(self):
        try: lv = self.query_one("#pl-side-list", ListView)
        except Exception: return
        eng = self.app.engine
        if not eng.queue: return
        self._w_all["pl-side-list"] = lv
        q = self._side_query.lower().strip()
        lv.clear()
        title_w = self._w("pl-side-title")
        if title_w: title_w.update(esc(eng.title or "TRT DINLE"))
        gap = " "
        num_w = 2
        dur_w = 7
        avail = 68
        used = 1 + 1 + num_w + 1 + 1 + dur_w
        rem = avail - used
        title_w = int(rem * 0.65)
        artist_w = rem - title_w
        for i, ep in enumerate(eng.queue):
            if q and q not in turkish_fold(track_label(ep)):
                continue
            dur_str = format_duration(ep.get("duration", 0) or 0)
            cur = i == eng.idx
            fav = is_favorite(ep)
            heart = "♥" if fav else " "
            num = f"{'▶' if cur else str(i+1):>{num_w}}"
            t = ep.get("title", "")
            a = ep.get("artist", "") or "—"
            title_part = esc(ellipsize(t, title_w))
            artist_part = esc(ellipsize(a, artist_w))
            dur_part = dur_str.rjust(dur_w)
            row = "".join([
                heart, gap,
                num, gap,
                title_part.ljust(title_w), gap,
                artist_part.ljust(artist_w), gap,
                dur_part,
            ])
            if cur:
                row = f"[{C_ACCENT}]{row}[/]"
            lv.append(ListItem(Label(row), name=str(i)))
        if eng.queue: lv.index = eng.idx
        self._side_populated = True

    def key_up(self, event):
        if self._list_focused():
            try: self._w("pl-side-list").action_cursor_up()
            except Exception: pass
        else:
            self.action_volume_up()
        event.stop()

    def key_down(self, event):
        if self._list_focused():
            try: self._w("pl-side-list").action_cursor_down()
            except Exception: pass
        else:
            self.action_volume_down()
        event.stop()

    def _list_focused(self):
        side = self._w("pl-side")
        if not side or side.has_class("hidden"):
            return False
        lv = self._w("pl-side-list")
        return lv is not None and self.focused is lv

    def _tick(self):
        eng = self.app.engine
        eng.drain()
        if not eng.queue: return
        if not self._side_populated:
            self._populate_side()
        if eng.idx != self._last_idx:
            self._render_header()
            self._populate_side()
        pos, dur = eng.position()
        w = self._cw(16)
        pct = (pos / dur) if dur > 0 else 0
        remaining = max(0, dur - pos) if dur > 0 else 0
        self._upd("pl-bar", progress_bar(pos, dur, w))
        self._upd("pl-bar-t",
            f"{format_duration(pos)} / {format_duration(dur)}   "
            f"[{C_MUTED}](~{format_duration(remaining)} kaldı)[/]")
        state = (eng.playing, eng.is_paused())
        self._set_play_icon()
        self._upd("pl-status", eng.status or "")

    def _update_vol(self):
        w = min(44, max(10, self._cw(30)))
        v = self.app.engine.volume
        if self.app.engine.muted:
            self._upd("pl-vol", f"[{C_MUTED}]🔇 Sessiz  ·  m ile aç[/]")
            return
        filled = round(w * (v / 100))
        bar_txt = "━" * filled
        rest = "─" * (w - filled)
        self._upd("pl-vol", f"🔊 [{C_ACCENT}]{bar_txt}[/][{C_MUTED}]{rest}[/]  {v}%")

    def _update_modes(self):
        eng = self.app.engine
        rep = {"off": "Kapalı", "all": "Tümü", "one": "Tek"}[eng.repeat]
        sh = "Açık" if eng.shuffle else "Kapalı"
        sh_c = C_ACCENT if eng.shuffle else C_MUTED
        rp_c = C_PRIMARY if eng.repeat != "off" else C_MUTED
        self._upd("pl-modes", f"[{sh_c}]🔀 Karışık: {sh}[/]      [{rp_c}]🔁 Tekrar: {rep}[/]")

    def action_toggle_play(self):
        self.app.engine.toggle_play()
        self._set_play_icon()
        self._upd("pl-status", self.app.engine.status or "")

    def action_seek_fwd(self): self.app.engine.seek(10)
    def action_seek_back(self): self.app.engine.seek(-10)

    def action_volume_up(self):
        self.app.engine.set_volume(self.app.engine.volume + 5)
        self._update_vol()

    def action_volume_down(self):
        self.app.engine.set_volume(self.app.engine.volume - 5)
        self._update_vol()

    def action_next_track(self):
        self.app.engine.next()
        self._render_header()
        self._populate_side()

    def action_prev_track(self):
        self.app.engine.prev()
        self._render_header()
        self._populate_side()

    def action_toggle_list(self):
        side = self._w("pl-side")
        if not side: return
        if side.has_class("hidden"):
            side.remove_class("hidden")
            self._populate_side()
            try: self._w("pl-side-list").focus()
            except Exception: pass
        else:
            side.add_class("hidden")
            self.set_focus(None)

    def action_toggle_shuffle(self):
        on = self.app.engine.toggle_shuffle()
        self._update_modes()
        self.app.notify(f"Karışık: {'Açık' if on else 'Kapalı'}", timeout=2)

    def action_cycle_repeat(self):
        self.app.engine.cycle_repeat()
        self._update_modes()
        lbl = {"off": "Kapalı", "all": "Tümü", "one": "Tek"}[self.app.engine.repeat]
        self.app.notify(f"Tekrar: {lbl}", timeout=2)

    def action_toggle_mute(self):
        self.app.engine.toggle_mute()
        self._update_vol()

    def action_toggle_favorite(self):
        eng = self.app.engine
        lv = self._w("pl-side-list")
        side = self._w("pl-side")
        if lv and side and not side.has_class("hidden") and lv.index is not None:
            idx = lv.index
            if 0 <= idx < len(eng.queue):
                ep = eng.queue[idx]
                on = toggle_favorite(ep)
                self._populate_side()
                self._render_header()
                lv.focus()
                self.app.notify(f"{'❤' if on else '♡'} {'Favorilere eklendi' if on else 'Favorilerden çıkarıldı'}", timeout=2)
                return
        ep = eng.current()
        if not ep:
            self.app.notify("Çalan parça yok", timeout=2)
            return
        on = toggle_favorite(ep)
        self._render_header()
        self._populate_side()
        self.app.notify(f"{'❤' if on else '♡'} {'Favorilere eklendi' if on else 'Favorilerden çıkarıldı'}", timeout=2)

    def action_remove_track(self):
        eng = self.app.engine
        lv = self._w("pl-side-list")
        side = self._w("pl-side")
        if lv and side and not side.has_class("hidden") and lv.index is not None:
            idx = lv.index
            if 0 <= idx < len(eng.queue):
                title = track_label(eng.queue[idx])
                eng.remove_from_queue(idx)
                self._populate_side()
                self._render_header()
                self.app.notify(f"Kaldırıldı: {ellipsize(title, 40)}", timeout=2)

    def action_sleep_menu(self):
        def _cb(minutes):
            if minutes > 0:
                self.app.engine.set_sleep(minutes)
                self.app.notify(f"⏰ Uyku zamanlayıcı: {minutes} dk", timeout=3)
            else:
                self.app.engine.set_sleep(0)
                self.app.notify("Uyku zamanlayıcı iptal edildi", timeout=3)
        self.app.push_screen(SleepTimerScreen(self.app.engine.sleep_remaining > 0), _cb)

    def action_back(self):
        self.app.pop_screen()

    def action_show_help(self):
        self.app.push_screen(HelpModalScreen())

    def action_focus_side_search(self):
        w = self._w("pl-side-search")
        if w: w.focus()

    @on(Input.Changed, "#pl-side-search")
    def _side_search(self, e):
        self._side_query = e.value
        self._populate_side()

    @on(Button.Pressed, "#pb-play")
    def _b_play(self): self.action_toggle_play()

    @on(Button.Pressed, "#pb-prev")
    def _b_prev(self): self.action_prev_track()

    @on(Button.Pressed, "#pb-next")
    def _b_next(self): self.action_next_track()

    @on(Button.Pressed, "#pb-back")
    def _b_back(self): self.action_seek_back()

    @on(Button.Pressed, "#pb-fwd")
    def _b_fwd(self): self.action_seek_fwd()

    @on(ListView.Selected, "#pl-side-list")
    def _side_sel(self, e):
        if e.item and e.item.name is not None:
            self.app.engine.jump(int(e.item.name))
            self._render_header()
            self._populate_side()


# ================== APP ==================

class TRTDinleApp(App):
    TITLE = "TRT Dinle"
    CSS = f"Screen {{ background: {C_BG}; }}"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.engine = PlayerEngine()

    def on_mount(self):
        self.push_screen(HomeScreen())

    def on_unmount(self):
        self.engine.terminate()


if __name__ == "__main__":
    TRTDinleApp().run()
