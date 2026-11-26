#!/usr/bin/env python3
"""Rewrite status.svg from the status site's status.json.

One GET to https://undecuple.github.io/me/status.json (no token, no other host). Refuses to change anything when the file is
missing, malformed, older than 6 hours or reports more than 3 failed API calls, so a bad run keeps the last good card.
Usage: python3 tools/profile.py [STATUS_JSON_URL_OR_PATH]   (default: the live site)
Writes status.svg in the current directory. Exit 0: written or unchanged. Exit 1: refused.
"""
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from xml.sax.saxutils import escape

SOURCE = "https://undecuple.github.io/me/status.json"
MAX_AGE_H = 6
MAX_ERRORS = 3
ROWS = 10                 # repositories on the card
REFRESH_H = 24            # rewrite the files at least this often, even when the data is the same
NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
USER = re.compile(r"^[A-Za-z0-9-]{1,39}$")
STATE = {"ok", "fail", "wait", "none", "unknown"}
COLOR = {"ok": "#a7d8b0", "fail": "#ac7f7b", "wait": "#997e95", "none": "#7a837c", "unknown": "#7a837c"}
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"


def fail(msg):
    print("refused:", msg, file=sys.stderr)
    sys.exit(1)


def load(src):
    try:
        if src.startswith("https://"):
            req = urllib.request.Request(src, headers={"User-Agent": "profile-card"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        with open(src, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        fail(f"cannot read {src}: {e}")


def pick(doc):
    """Validated rows: own (non-fork) public repositories, newest push first. Anything odd is dropped, never trusted."""
    try:
        gen = datetime.strptime(doc["generated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        user, errors, repos = doc["user"], int(doc.get("errors", 0)), doc["repos"]
    except (KeyError, TypeError, ValueError):
        fail("status.json has an unexpected shape")
    if not USER.match(user):
        fail("bad user name")
    if errors > MAX_ERRORS:
        fail(f"{errors} failed API calls in status.json")
    age = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
    if age > MAX_AGE_H:
        fail(f"status.json is {age:.1f} h old")
    rows = []
    for r in repos:
        try:
            name, fork = r["name"], bool(r["fork"])
            ci = r["ci"]["state"] if r["ci"]["state"] in STATE else "unknown"
            pushed = r["pushed_at"][:10]
            rel = (r.get("release") or {}).get("tag") or "-"
            stars = int(r["stars"])
        except (KeyError, TypeError, ValueError):
            continue
        if fork or not NAME.match(name) or not re.fullmatch(r"\d{4}-\d\d-\d\d", pushed) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,40}|-", rel):
            continue
        rows.append((name, ci, pushed, rel, stars))
    rows.sort(key=lambda x: x[2], reverse=True)
    if not rows:
        fail("no usable repositories")
    return user, gen, rows


def svg(user, gen, rows):
    shown = rows[:ROWS]
    w, rh = 640, 24
    h = 58 + len(shown) * rh + 30
    t = lambda x, y, txt, fill, size=12, anchor="start", weight=400: (
        f'<text x="{x}" y="{y}" fill="{fill}" font-family="{MONO}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(txt)}</text>')
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w * 2}" height="{h * 2}" role="img" aria-labelledby="t">',
           f'<title id="t">Status of {escape(user)} public repositories, generated {gen:%Y-%m-%d %H:%M} UTC</title>',
           f'<rect width="{w}" height="{h}" fill="#0c0d0e"/>',
           f'<rect x="3" y="3" width="{w - 6}" height="{h - 6}" fill="none" stroke="#554a62" stroke-width="2"/>',
           t(20, 32, f"{user.upper()} // STATUS", "#8288a0", 13, weight=600),
           t(w - 20, 32, f"{len(rows)} repositories", "#7a837c", 11, "end")]
    for label, x, a in (("REPOSITORY", 20, "start"), ("CI", 300, "start"), ("PUSH", 380, "start"), ("RELEASE", 470, "start"), ("STARS", w - 20, "end")):
        out.append(t(x, 54, label, "#7a837c", 10, a))
    for i, (name, ci, pushed, rel, stars) in enumerate(shown):
        y = 58 + (i + 1) * rh - 6
        out += [t(20, y, name if len(name) <= 26 else name[:25] + "\u2026", "#9f9e99"),
                f'<rect x="300" y="{y - 9}" width="9" height="9" fill="{COLOR[ci]}"/>',
                t(315, y, ci, COLOR[ci]), t(380, y, pushed, "#7a837c"), t(470, y, rel, "#7a837c"), t(w - 20, y, str(stars), "#9f9e99", anchor="end")]
    out += [t(20, h - 14, f"generated {gen:%Y-%m-%d %H:%M} UTC", "#7a837c", 10), "</svg>", ""]
    return "\n".join(out)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else SOURCE
    user, gen, rows = pick(load(src))
    digest = hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]
    new_svg = svg(user, gen, rows)
    new_svg = new_svg.replace("<svg ", f'<svg data-digest="{digest}" ', 1)
    try:
        old = open("status.svg", encoding="utf-8").read()
        m = re.search(r'data-digest="([0-9a-f]+)".*?generated (\d{4}-\d\d-\d\d \d\d:\d\d) UTC', old, re.S)
        if m and m.group(1) == digest:
            old_gen = datetime.strptime(m.group(2), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            if (gen - old_gen).total_seconds() / 3600 < REFRESH_H:
                print("unchanged")
                return
    except OSError:
        pass
    with open("status.svg", "w", encoding="utf-8", newline="\n") as f:
        f.write(new_svg)
    print(f"wrote status.svg: {len(rows[:ROWS])} of {len(rows)} repositories on the card")


if __name__ == "__main__":
    main()
