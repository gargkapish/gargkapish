#!/usr/bin/env python3
"""Build the custom SVGs for the GitHub profile README.

Run from anywhere:   python3 scripts/build_profile.py            (fetches live DSA stats)
                     python3 scripts/build_profile.py --offline  (uses data/dsa-cache.json)

Reads   data/profile.json, data/icons.json, data/dsa-cache.json
Writes  assets/header.svg, assets/stack.svg, assets/btn-*.svg,
        assets/dsa.svg, assets/building.svg, data/dsa-cache.json,
        and the <!-- building:start --> ... <!-- building:end --> block in README.md

Standard library only, so it runs on a bare GitHub Actions runner.
Network failures never fail the build: the last good numbers are reused.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
CFG = json.loads((DATA / "profile.json").read_text(encoding="utf-8"))
BRAND_ICONS = json.loads((DATA / "icons.json").read_text(encoding="utf-8"))
CACHE_FILE = DATA / "dsa-cache.json"

# ---------------------------------------------------------------- palette ---
P = CFG["palette"]
VOID, VIOLET, CYAN, TEXT, MUTED = P["void"], P["violet"], P["cyan"], P["text"], P["muted"]


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def mix(a: str, b: str, t: float) -> str:
    """Linear sRGB blend of two hex colours, t=0 -> a, t=1 -> b."""
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


PANEL = mix(VOID, VIOLET, 0.18)    # violet-tinted black for inner surfaces
VLIFT = mix(VIOLET, TEXT, 0.42)    # violet light enough to see on black (graphics only)
DEEP = mix(VIOLET, CYAN, 0.55)     # the blue midpoint of the violet -> cyan ramp

SANS = "'Segoe UI', Inter, -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Ubuntu, Arial, sans-serif"
MONO = ("'JetBrains Mono', 'SFMono-Regular', 'SF Mono', ui-monospace, Menlo, Consolas, "
        "'Liberation Mono', 'DejaVu Sans Mono', monospace")
MONO_ADV = 0.6  # advance of one monospace glyph, in em; textLength pins it on every OS

# ------------------------------------------------------------------ icons ---
# Hand-drawn 24x24 line icons for things with no brand mark.
LINE_ICONS = {
    "person": "M12 12.5a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4.5 20.5c.8-3.6 3.9-5.5 7.5-5.5s6.7 1.9 7.5 5.5",
    "mail": "M4.5 5h15A1.5 1.5 0 0 1 21 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5v-11A1.5 1.5 0 0 1 4.5 5ZM3.6 6.4 12 13l8.4-6.6",
    "brackets": "M8.5 7 3.5 12l5 5M15.5 7l5 5-5 5M13.6 4.5l-3.2 15",
    "chart": "M4 4v16h16M8 15.5l3.5-4.5 3 2.5L19 7.5",
}


def icon(name: str, x: float, y: float, size: float, color: str) -> str:
    s = size / 24
    if name in BRAND_ICONS:
        return (f'<path transform="translate({x:.1f} {y:.1f}) scale({s:.4f})" '
                f'd="{BRAND_ICONS[name]}" fill="{color}"/>')
    return (f'<path transform="translate({x:.1f} {y:.1f}) scale({s:.4f})" d="{LINE_ICONS[name]}" '
            f'fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


# ----------------------------------------------------------- primitives ---
def mono(txt: str, x: float, y: float, size: float, fill: str, extra: str = "") -> str:
    """Monospace text whose width is pinned, so layout holds whatever font loads."""
    w = len(txt) * size * MONO_ADV
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="m" font-size="{size}" fill="{fill}" '
            f'textLength="{w:.1f}" lengthAdjust="spacing" {extra}>{escape(txt)}</text>')


def mono_w(txt: str, size: float) -> float:
    return len(txt) * size * MONO_ADV


def label(txt: str, x: float, y: float) -> str:
    """Section label used on every card:  › stack"""
    return (f'<path d="M{x:.1f} {y - 9:.1f}l4.5 4.5-4.5 4.5" fill="none" stroke="{CYAN}" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            + mono(txt, x + 12, y, 12.5, MUTED))


BASE_CSS = f"""
.s{{font-family:{SANS}}}
.m{{font-family:{MONO};white-space:pre}}
@media (prefers-reduced-motion: reduce){{*{{animation:none!important}}}}
"""


def svg(w: int, h: int, title: str, desc: str, body: str, css: str = "", defs: str = "",
        slab: bool = True) -> str:
    frame = ""
    if slab:
        frame = (f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="16" fill="{VOID}" '
                 f'stroke="{VIOLET}" stroke-width="1.5"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-labelledby="title desc">'
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(desc)}</desc>'
            f'<defs><style>{BASE_CSS}{css}</style>{defs}</defs>{frame}{body}</svg>\n')


# ---------------------------------------------------------------- header ---
def build_header() -> str:
    W, H = 900, 300
    lines = CFG["typing"]
    fs, x0, base = 19, 92, 236
    cw = fs * MONO_ADV

    # Timeline: each line types, holds, erases, pauses.
    slots = []
    for ln in lines:
        n = len(ln)
        t_type = min(2.4, 0.055 * n)
        slots.append((n, t_type, 2.6, 0.5, 0.45))
    total = sum(sum(s[1:]) for s in slots)

    css = [f"""
.glow{{animation:breathe 7s ease-in-out infinite}}
@keyframes breathe{{0%,100%{{opacity:.78}}50%{{opacity:1}}}}
.ping{{transform-box:fill-box;transform-origin:center;animation:ping 2.4s cubic-bezier(0,0,.2,1) infinite}}
@keyframes ping{{0%{{transform:scale(1);opacity:.75}}80%,100%{{transform:scale(2.6);opacity:0}}}}
.cur{{animation-name:var(--k)}}
"""]
    groups = []
    t0 = 0.0
    for i, (ln, (n, t_type, hold, erase, gap)) in enumerate(zip(lines, slots)):
        pct = lambda t: f"{t / total * 100:.3f}%"
        a, b, c, d = t0, t0 + t_type, t0 + t_type + hold, t0 + t_type + hold + erase
        end = d + gap
        shift = n * cw
        # group is visible only during its slot
        vis = (f"@keyframes v{i}{{0%{{opacity:{1 if i == 0 else 0}}}"
               f"{pct(a)}{{opacity:1}}{pct(end)}{{opacity:0}}100%{{opacity:0}}}}")
        mv = (f"@keyframes t{i}{{0%{{transform:translateX(0)}}"
              f"{pct(a)}{{transform:translateX(0);animation-timing-function:steps({n},end)}}"
              f"{pct(b)}{{transform:translateX({shift:.1f}px);animation-timing-function:linear}}"
              f"{pct(c)}{{transform:translateX({shift:.1f}px);animation-timing-function:steps({n},end)}}"
              f"{pct(d)}{{transform:translateX(0)}}100%{{transform:translateX(0)}}}}")
        css.append(vis + mv)
        css.append(f".g{i}{{animation:v{i} {total:.2f}s step-end infinite}}"
                   f".k{i}{{animation:t{i} {total:.2f}s linear infinite}}")
        xs = " ".join(f"{x0 + j * cw:.1f}" for j in range(n))
        groups.append(
            f'<g class="g{i}">'
            f'<text class="m" x="{xs}" y="{base}" font-size="{fs}" fill="{TEXT}">{escape(ln)}</text>'
            f'<g class="k{i}"><rect x="{x0 - 2}" y="{base - 26}" width="{W - x0 - 40}" height="36" fill="{PANEL}"/>'
            f'<rect x="{x0 + 1}" y="{base - 18}" width="{cw - 2:.1f}" height="23" rx="1.5" fill="{CYAN}"/></g>'
            f'</g>')
        t0 = end
    # Reduced motion: show the first line, static.
    css.append("@media (prefers-reduced-motion: reduce){"
               + "".join(f".g{i}{{opacity:{1 if i == 0 else 0}}}" for i in range(len(lines)))
               + f".k0{{transform:translateX({len(lines[0]) * cw:.1f}px)}}}}")

    status = CFG["status"]
    st_size = 12.5
    st_x = W - 40 - mono_w(status, st_size)

    defs = f"""
<radialGradient id="gv" cx="88%" cy="-8%" r="78%"><stop offset="0" stop-color="{VIOLET}" stop-opacity="1"/>
<stop offset=".55" stop-color="{VIOLET}" stop-opacity=".35"/><stop offset="1" stop-color="{VIOLET}" stop-opacity="0"/></radialGradient>
<radialGradient id="gc" cx="4%" cy="118%" r="55%"><stop offset="0" stop-color="{CYAN}" stop-opacity=".16"/>
<stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></radialGradient>
<pattern id="grid" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M28 0H0V28" fill="none" stroke="{VLIFT}" stroke-width=".6"/></pattern>
<linearGradient id="gf" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".6" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity=".5"/></linearGradient>
<mask id="gm"><rect width="{W}" height="{H}" fill="url(#gf)"/></mask>
<linearGradient id="ng" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{TEXT}"/><stop offset=".62" stop-color="{TEXT}"/><stop offset="1" stop-color="{CYAN}"/></linearGradient>
<clipPath id="card"><rect x="1.5" y="1.5" width="{W - 3}" height="{H - 3}" rx="15"/></clipPath>
<clipPath id="bar"><rect x="40" y="200" width="{W - 80}" height="58" rx="12"/></clipPath>
"""
    body = f"""
<g clip-path="url(#card)">
<rect width="{W}" height="{H}" fill="url(#grid)" mask="url(#gm)"/>
<rect class="glow" width="{W}" height="{H}" fill="url(#gv)"/>
<rect width="{W}" height="{H}" fill="url(#gc)"/>
</g>
{label("~/" + CFG["github"], 40, 54)}
<circle class="ping" cx="{st_x - 13:.1f}" cy="49.5" r="4" fill="{CYAN}"/>
<circle cx="{st_x - 13:.1f}" cy="49.5" r="4" fill="{CYAN}"/>
{mono(status, st_x, 54, st_size, TEXT)}
<text x="38" y="142" class="s" font-size="64" font-weight="800" letter-spacing="-1.5" fill="url(#ng)">{escape(CFG["name"])}</text>
<text x="41" y="176" class="s" font-size="17" fill="{MUTED}">{escape(CFG["tagline"])}</text>
<rect x="40" y="200" width="{W - 80}" height="58" rx="12" fill="{PANEL}" stroke="{VIOLET}" stroke-width="1.5"/>
<path d="M60 222l7.5 7.5-7.5 7.5" fill="none" stroke="{CYAN}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
<g clip-path="url(#bar)">{''.join(groups)}</g>
"""
    desc = f"{CFG['name']} — " + " / ".join(lines)
    return svg(W, H, CFG["name"], desc, body, "".join(css), defs)


# ----------------------------------------------------------------- stack ---
def chip(x: float, y: float, txt: str, ic: str, learning: bool) -> tuple[str, float]:
    fs, h = 14, 38
    w = 14 + 18 + 10 + mono_w(txt, fs) + 16
    stroke = (f'stroke="{VLIFT}" stroke-width="1.2" stroke-dasharray="4 3.5"' if learning
              else f'stroke="{VIOLET}" stroke-width="1.5"')
    out = (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" rx="10" fill="{PANEL}" {stroke}/>'
           + icon(ic, x + 14, y + 10, 18, MUTED if learning else CYAN)
           + mono(txt, x + 42, y + 24.5, fs, TEXT))
    return out, w


def build_stack() -> str:
    W, H = 900, 222
    parts = [label("stack", 36, 46)]
    rows = [("using", CFG["stack"]["use"], False, 88), ("learning", CFG["stack"]["learning"], True, 166)]
    for name, items, learning, y in rows:
        parts.append(mono(name, 36, y - 14, 12, MUTED))
        x = 36.0
        for it in items:
            c, w = chip(x, y, it["label"], it["icon"], learning)
            parts.append(c)
            x += w + 10
        if x > W - 30:
            raise SystemExit(f"stack row '{name}' is too wide ({x:.0f}px): shorten labels or split the row")
    names = ", ".join(i["label"] for i in CFG["stack"]["use"])
    learn = ", ".join(i["label"] for i in CFG["stack"]["learning"])
    return svg(W, H, "Stack", f"Using: {names}. Learning: {learn}.", "".join(parts))


# --------------------------------------------------------------- buttons ---
def build_button(link: dict) -> str:
    fs, h = 14, 44
    w = round(15 + 18 + 11 + mono_w(link["label"], fs) + 17)
    defs = (f'<linearGradient id="bd" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{VLIFT}"/>'
            f'<stop offset="1" stop-color="{CYAN}" stop-opacity=".65"/></linearGradient>')
    body = (f'<rect x=".75" y=".75" width="{w - 1.5}" height="{h - 1.5}" rx="11" fill="{VOID}" '
            f'stroke="url(#bd)" stroke-width="1.5"/>'
            + icon(link["icon"], 15, 13, 18, CYAN)
            + mono(link["label"], 44, 27.5, fs, TEXT))
    return svg(w, h, link["label"], f"{link['label']}: {link['url']}", body, defs=defs, slab=False)


# ------------------------------------------------------------------- dsa ---
UA = {"User-Agent": f"{CFG['github']}-profile-readme/1.0 (+https://github.com/{CFG['github']})"}


def http_json(url: str, payload: dict | None = None) -> dict:
    headers = dict(UA)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers.update({"Content-Type": "application/json", "Referer": "https://leetcode.com"})
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_leetcode(user: str) -> dict:
    q = {"query": "query($u:String!){matchedUser(username:$u){submitStatsGlobal{acSubmissionNum{difficulty count}}}}",
         "variables": {"u": user}}
    d = http_json("https://leetcode.com/graphql", q)
    nums = {x["difficulty"]: x["count"] for x in d["data"]["matchedUser"]["submitStatsGlobal"]["acSubmissionNum"]}
    return {"total": nums["All"], "easy": nums["Easy"], "medium": nums["Medium"], "hard": nums["Hard"]}


def fetch_codeforces(handle: str) -> dict:
    h = urllib.parse.quote(handle)
    api = "https://codeforces.com/api/"
    info = http_json(f"{api}user.info?handles={h}")
    hist = http_json(f"{api}user.rating?handle={h}")
    subs = http_json(f"{api}user.status?handle={h}")
    for r in (info, hist, subs):
        if r.get("status") != "OK":
            raise RuntimeError(r.get("comment", "codeforces api error"))
    u = info["result"][0]
    solved = {(s["problem"].get("contestId"), s["problem"].get("index"))
              for s in subs["result"] if s.get("verdict") == "OK"}
    return {"rating": u.get("rating"), "maxRating": u.get("maxRating"),
            "contests": len(hist["result"]), "history": [x["newRating"] for x in hist["result"]],
            "solved": len(solved)}


def load_dsa(offline: bool) -> dict:
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    if offline:
        return cache
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for key, fn, who in (("leetcode", fetch_leetcode, CFG["leetcode"]),
                         ("codeforces", fetch_codeforces, CFG["codeforces"])):
        try:
            cache[key] = {**fn(who), "synced": now}
            print(f"  {key}: refreshed")
        except Exception as e:  # network, API shape, rate limit: keep the last good numbers
            print(f"  {key}: kept cached numbers ({type(e).__name__}: {e})", file=sys.stderr)
    CACHE_FILE.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    return cache


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "not synced yet"
    return "synced " + datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y").lower()


def build_dsa(d: dict) -> str:
    W, H = 900, 252
    css = f"""
.grow{{transform-box:fill-box;transform-origin:left center;animation:grow 1.3s cubic-bezier(.2,.8,.2,1) both}}
@keyframes grow{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}
.draw{{stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1.8s .2s cubic-bezier(.4,0,.2,1) forwards}}
@keyframes draw{{to{{stroke-dashoffset:0}}}}
.fade{{opacity:0;animation:fade .8s 1.2s ease forwards}}
@keyframes fade{{to{{opacity:1}}}}
.ping{{transform-box:fill-box;transform-origin:center;animation:ping 2.4s 2s cubic-bezier(0,0,.2,1) infinite}}
@keyframes ping{{0%{{transform:scale(1);opacity:.8}}80%,100%{{transform:scale(3);opacity:0}}}}
"""
    defs = (f'<linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{VIOLET}" stop-opacity=".95"/>'
            f'<stop offset="1" stop-color="{VIOLET}" stop-opacity="0"/></linearGradient>'
            f'<linearGradient id="line" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{VLIFT}"/>'
            f'<stop offset="1" stop-color="{CYAN}"/></linearGradient>')
    lc, cf = d.get("leetcode", {}), d.get("codeforces", {})
    out = [f'<line x1="450" y1="34" x2="450" y2="{H - 34}" stroke="{VIOLET}" stroke-width="1.5"/>']

    # LeetCode
    L = 36
    out.append(label("leetcode", L, 48))
    total = lc.get("total")
    out.append(f'<text x="{L - 2}" y="124" class="s" font-size="58" font-weight="800" letter-spacing="-1.5" '
               f'fill="{TEXT}">{total if total is not None else "—"}</text>')
    out.append(f'<text x="{L}" y="152" class="s" font-size="15" fill="{MUTED}">problems solved</text>')
    segs = [("Easy", lc.get("easy", 0) or 0, CYAN), ("Medium", lc.get("medium", 0) or 0, DEEP),
            ("Hard", lc.get("hard", 0) or 0, VLIFT)]
    bar_w, gap, by = 378.0, 3.0, 174
    tot = sum(s[1] for s in segs) or 1
    shown = [s for s in segs if s[1] > 0]
    usable = bar_w - gap * (len(shown) - 1)
    x = L
    out.append(f'<rect x="{L}" y="{by}" width="{bar_w}" height="10" rx="5" fill="{PANEL}"/>')
    for i, (name, n, col) in enumerate(shown):
        w = max(6.0, usable * n / tot)
        if i == len(shown) - 1:
            w = L + bar_w - x
        out.append(f'<rect class="grow" style="animation-delay:{.15 * i:.2f}s" x="{x:.1f}" y="{by}" '
                   f'width="{w:.1f}" height="10" rx="5" fill="{col}"/>')
        x += w + gap
    lx = L
    for name, n, col in segs:
        txt = f"{name} {n}"
        out.append(f'<circle cx="{lx + 4.5}" cy="{by + 35.5}" r="4.5" fill="{col}"/>')
        out.append(mono(txt, lx + 15, by + 40, 12.5, TEXT))
        lx += 15 + mono_w(txt, 12.5) + 24

    # Codeforces
    R = 486
    out.append(label("codeforces", R, 48))
    synced = fmt_date(cf.get("synced") or lc.get("synced"))
    out.append(mono(synced, W - 36 - mono_w(synced, 11.5), 48, 11.5, MUTED))
    rating = cf.get("rating")
    out.append(f'<text x="{R - 2}" y="124" class="s" font-size="58" font-weight="800" letter-spacing="-1.5" '
               f'fill="{TEXT}">{rating if rating is not None else "unrated"}</text>')
    bits = ["rating"]
    if cf.get("maxRating"):
        bits.append(f"peak {cf['maxRating']}")
    if cf.get("contests") is not None:
        bits.append(f"{cf['contests']} contests")
    if cf.get("solved") is not None:
        bits.append(f"{cf['solved']} solved")
    out.append(f'<text x="{R}" y="152" class="s" font-size="15" fill="{MUTED}">{escape(" · ".join(bits))}</text>')

    hist = cf.get("history") or []
    gx0, gx1, gy0, gy1 = R, W - 36, 172, 220
    if len(hist) >= 2:
        lo, hi = min(hist), max(hist)
        span = max(hi - lo, 1)
        pts = [(gx0 + (gx1 - gx0) * i / (len(hist) - 1), gy1 - (gy1 - gy0) * (v - lo) / span) for i, v in enumerate(hist)]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        area = f"M{pts[0][0]:.1f},{gy1} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f},{gy1} Z"
        out.append(f'<path class="fade" d="{area}" fill="url(#area)"/>')
        out.append(f'<polyline class="draw" pathLength="1" points="{poly}" fill="none" stroke="url(#line)" '
                   f'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>')
        for x, y in pts[:-1]:
            out.append(f'<circle class="fade" cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{VOID}" stroke="{VLIFT}" stroke-width="1.5"/>')
        lx_, ly_ = pts[-1]
        out.append(f'<circle class="ping" cx="{lx_:.1f}" cy="{ly_:.1f}" r="4" fill="{CYAN}"/>')
        out.append(f'<circle class="fade" cx="{lx_:.1f}" cy="{ly_:.1f}" r="4.5" fill="{CYAN}"/>')
        out.append(mono(f"{lo}", gx0, gy1 + 18, 11, MUTED))
    else:
        out.append(mono("rating graph appears after 2 rated contests", gx0, 200, 12, MUTED))

    desc = (f"LeetCode: {total} solved ({lc.get('easy')} easy, {lc.get('medium')} medium, {lc.get('hard')} hard). "
            f"Codeforces: rating {rating}, peak {cf.get('maxRating')}, {cf.get('contests')} contests.")
    return svg(W, H, "DSA", desc, "".join(o for o in out if o), css, defs)


# -------------------------------------------------------------- building ---
def build_building() -> str:
    W, H = 900, 206
    b = CFG["building"]
    projects = b["projects"]
    n = len(projects)
    x0, x1, ty = 110, W - 110, 106
    xs = [x0 + (x1 - x0) * i / max(n - 1, 1) for i in range(n)]
    css = """
.ping{transform-box:fill-box;transform-origin:center;animation:ping 2.4s cubic-bezier(0,0,.2,1) infinite}
@keyframes ping{0%{transform:scale(1);opacity:.8}80%,100%{transform:scale(2.4);opacity:0}}
.draw{stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1.6s .2s cubic-bezier(.4,0,.2,1) forwards}
@keyframes draw{to{stroke-dashoffset:0}}
"""
    out = [label(b["program"].lower(), 36, 46)]
    win = b["window"].lower()
    out.append(mono(win, W - 36 - mono_w(win, 12), 46, 12, MUTED))
    out.append(f'<line x1="{x0}" y1="{ty}" x2="{x1}" y2="{ty}" stroke="{VLIFT}" stroke-opacity=".45" '
               f'stroke-width="2" stroke-dasharray="2 6" stroke-linecap="round"/>')
    reached = -1
    for i, p in enumerate(projects):
        if p["status"] in ("shipped", "building"):
            reached = i
    if reached > 0:
        out.append(f'<line class="draw" pathLength="1" x1="{x0}" y1="{ty}" x2="{xs[reached]:.1f}" y2="{ty}" '
                   f'stroke="{CYAN}" stroke-width="2.5" stroke-linecap="round"/>')
    first_next = True
    for i, (x, p) in enumerate(zip(xs, projects)):
        st = p["status"]
        out.append(mono(f"{i + 1:02d}", x - mono_w("00", 12) / 2, ty - 26, 12, MUTED))
        if st == "shipped":
            out.append(f'<circle cx="{x:.1f}" cy="{ty}" r="12" fill="{CYAN}"/>'
                       f'<path d="M{x - 5:.1f} {ty}l3.4 3.4 6.6-6.8" fill="none" stroke="{VOID}" stroke-width="2.6" '
                       f'stroke-linecap="round" stroke-linejoin="round"/>')
            tag, col = "shipped", CYAN
        elif st == "building":
            out.append(f'<circle class="ping" cx="{x:.1f}" cy="{ty}" r="12" fill="{CYAN}" fill-opacity=".5"/>'
                       f'<circle cx="{x:.1f}" cy="{ty}" r="12" fill="{VOID}" stroke="{CYAN}" stroke-width="3"/>'
                       f'<circle cx="{x:.1f}" cy="{ty}" r="5" fill="{CYAN}"/>')
            tag, col = "building now", CYAN
        else:
            out.append(f'<circle cx="{x:.1f}" cy="{ty}" r="11" fill="{VOID}" stroke="{VLIFT}" stroke-width="2"/>')
            tag, col = ("up next", MUTED) if first_next else ("queued", MUTED)
            first_next = False
        out.append(f'<text x="{x:.1f}" y="{ty + 42}" text-anchor="middle" class="s" font-size="16.5" '
                   f'font-weight="600" fill="{TEXT}">{escape(p["name"])}</text>')
        out.append(mono(tag, x - mono_w(tag, 12) / 2, ty + 64, 12, col))
    desc = f"{b['program']} ({b['window']}): " + ", ".join(f"{p['name']} ({p['status']})" for p in projects)
    return svg(W, H, "Currently building", desc, "".join(out), css)


# -------------------------------------------------------------- featured ---
def wrap(txt: str, limit: int) -> list[str]:
    lines, cur = [], ""
    for word in txt.split():
        if cur and len(cur) + 1 + len(word) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + ([cur] if cur else [])


def build_featured(item: dict) -> str:
    W = 900
    desc_lines = wrap(item["desc"], 92)
    ty = 128 + 24 * (len(desc_lines) - 1) + 24  # top of the tag row
    H = ty + 28 + 30
    css = """
.ping{transform-box:fill-box;transform-origin:center;animation:ping 2.4s cubic-bezier(0,0,.2,1) infinite}
@keyframes ping{0%{transform:scale(1);opacity:.8}80%,100%{transform:scale(2.6);opacity:0}}
"""
    out = [label("featured", 36, 46)]
    host = item["url"].split("//", 1)[-1].rstrip("/")
    tag = "live" if item["url"].startswith("http") and "github.com" not in item["url"] else "repo"
    tx = W - 36 - mono_w(tag, 12)
    out.append(f'<circle class="ping" cx="{tx - 12:.1f}" cy="41.5" r="4" fill="{CYAN}"/>'
               f'<circle cx="{tx - 12:.1f}" cy="41.5" r="4" fill="{CYAN}"/>')
    out.append(mono(tag, tx, 46, 12, CYAN))
    out.append(f'<text x="35" y="94" class="s" font-size="28" font-weight="700" letter-spacing="-.4" '
               f'fill="{TEXT}">{escape(item["title"])}</text>')
    for i, ln in enumerate(desc_lines):
        out.append(f'<text x="36" y="{126 + 24 * i}" class="s" font-size="15.5" fill="{MUTED}">{escape(ln)}</text>')
    x = 36.0
    for t in item.get("tags", []):
        w = mono_w(t, 12) + 22
        out.append(f'<rect x="{x:.1f}" y="{ty}" width="{w:.1f}" height="28" rx="8" fill="{PANEL}" '
                   f'stroke="{VIOLET}" stroke-width="1.3"/>')
        out.append(mono(t, x + 11, ty + 18.5, 12, TEXT))
        x += w + 8
    cx, cy = W - 62, 86
    out.append(f'<circle cx="{cx}" cy="{cy}" r="24" fill="{PANEL}" stroke="{VIOLET}" stroke-width="1.5"/>'
               f'<path d="M{cx - 6} {cy + 6}l12-12M{cx - 4} {cy - 6}h10v10" fill="none" stroke="{CYAN}" '
               f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>')
    return svg(W, H, item["title"], f"{item['title']} ({host}): {item['desc']}", "".join(out), css)


# ---------------------------------------------------------------- readme ---
def update_readme() -> None:
    path = ROOT / "README.md"
    if not path.exists():
        return
    start, end = "<!-- building:start -->", "<!-- building:end -->"
    text = path.read_text(encoding="utf-8")
    if start not in text or end not in text:
        return
    links = []
    for p in CFG["building"]["projects"]:
        if p.get("repo"):
            url = p["repo"] if p["repo"].startswith("http") else f"https://github.com/{CFG['github']}/{p['repo']}"
            links.append(f'<a href="{url}"><b>{escape(p["name"])}</b></a>')
        else:
            links.append(escape(p["name"]))
    if any(p.get("repo") for p in CFG["building"]["projects"]):
        block = f'{start}\n<p align="center"><sub>repos: {" · ".join(links)}</sub></p>\n{end}'
    else:  # nothing shipped yet: the roadmap card already names the projects
        block = f"{start}\n{end}"
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    path.write_text(head + block + tail, encoding="utf-8")


# ------------------------------------------------------------------ main ---
def main() -> None:
    offline = "--offline" in sys.argv
    ASSETS.mkdir(exist_ok=True)
    files = {
        "header.svg": build_header(),
        "stack.svg": build_stack(),
        "building.svg": build_building(),
        "dsa.svg": build_dsa(load_dsa(offline)),
    }
    for link in CFG["links"]:
        files[f"btn-{link['id']}.svg"] = build_button(link)
    for item in CFG.get("featured", []):
        files[f"featured-{item['id']}.svg"] = build_featured(item)
    for name, content in files.items():
        (ASSETS / name).write_text(content, encoding="utf-8")
        print(f"  wrote assets/{name}")
    update_readme()


if __name__ == "__main__":
    main()
