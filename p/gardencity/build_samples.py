#!/usr/bin/env python3
"""
GardenCity Playlist-Seite aus Rekordbox-DB aktualisieren.

Macht folgendes:
  1. Liest die Playlists unter dem Ordner "Hochzeiten GardenCity" aus der Rekordbox-DB
  2. Erzeugt 2s-MP3-Samples (aus der Songmitte) aus den lokalen Files
  3. Generiert p/gardencity/index.html neu (behält Cover-Art der alten Seite)

Usage:
  python3 p/gardencity/build_samples.py

Requirements:
  pip3 install pyrekordbox
  brew install ffmpeg
"""

import os
import re
import html as html_mod
import subprocess
import hashlib
from pathlib import Path

from pyrekordbox.db6.database import Rekordbox6Database
from pyrekordbox.db6.tables import DjmdContent, DjmdSongPlaylist, DjmdPlaylist, DjmdKey
from sqlalchemy import select

REPO_DIR = Path(__file__).resolve().parent.parent.parent  # .../DJERMN.github.io
HTML_PATH = REPO_DIR / "p" / "gardencity" / "index.html"
SAMPLES_DIR = REPO_DIR / "p" / "gardencity" / "samples"

DB_PATH = "/Users/bariser/Library/Pioneer/rekordbox/master.db"
FOLDER_NAME = "Hochzeiten GardenCity"  # Rekordbox-Ordner mit den Playlists

SAMPLE_DURATION = 2      # Sekunden
SAMPLE_OFFSET_RATIO = 0.5  # Start in der Songmitte

PLAY_BTN = ('<button class="play-btn" onclick="playPreview(event,this)" aria-label="Vorschau">'
            '<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">'
            '<path d="M8 5v14l11-7z"/></svg></button>')


# ── Rekordbox lesen ──────────────────────────────────────────────
def read_rekordbox_data():
    db = Rekordbox6Database(str(DB_PATH))

    # Ordner finden
    folder = None
    for row in db.session.execute(select(DjmdPlaylist)).fetchall():
        if row[0].Name == FOLDER_NAME:
            folder = row[0]
            break
    if folder is None:
        raise SystemExit(f"Ordner '{FOLDER_NAME}' nicht in Rekordbox gefunden")

    # Playlists im Ordner (nach Seq sortiert)
    pl_stmt = (select(DjmdPlaylist)
               .where(DjmdPlaylist.ParentID == str(folder.ID))
               .order_by(DjmdPlaylist.Seq))
    playlist_rows = db.session.execute(pl_stmt).fetchall()

    key_cache = {}
    for row in db.session.execute(select(DjmdKey)).fetchall():
        key_cache[row[0].ID] = row[0].ScaleName

    playlists = []
    for row in playlist_rows:
        pl = row[0]
        stmt = (
            select(DjmdContent, DjmdSongPlaylist)
            .join(DjmdSongPlaylist, DjmdContent.ID == DjmdSongPlaylist.ContentID)
            .where(DjmdSongPlaylist.PlaylistID == str(pl.ID))
            .order_by(DjmdSongPlaylist.TrackNo)
        )
        result = db.session.execute(stmt).fetchall()

        tracks = []
        for content, song_pl in result:
            dur = content.Length or 0
            tracks.append({
                "id": content.ID,
                "title": content.Title or "Unknown",
                "artist": content.ArtistName or "Unknown",
                "bpm": float(content.BPM) / 100 if content.BPM else 0,
                "key": key_cache.get(content.KeyID, "?"),
                "duration": f"{int(dur // 60)}:{int(dur % 60):02d}" if dur else "?",
                "dur_sec": dur,
                "path": content.FolderPath or "",
            })
        playlists.append({"name": pl.Name, "tracks": tracks})

    db.close()
    return playlists


# ── Alte Seite parsen (Cover-Art übernehmen) ─────────────────────
def parse_existing_html():
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    cover_map = {}
    card_pattern = re.compile(r'data-title="([^"]*)"\s+data-artist="([^"]*)"')
    for m in card_pattern.finditer(content):
        title = html_mod.unescape(m.group(1)).strip().lower()
        artist = html_mod.unescape(m.group(2)).strip().lower()
        key = f"{title}|||{artist}"

        tag_end = content.find(">", m.end()) + 1
        rest = content[tag_end:tag_end + 800]
        img = re.search(r'<img src="([^"]+)"', rest)
        cover_map[key] = img.group(1) if img else ""

    return cover_map, content


# ── Samples erzeugen ─────────────────────────────────────────────
def sample_filename(track_id):
    return hashlib.md5(f"garden_{track_id}".encode()).hexdigest()[:12] + ".mp3"


def generate_sample(src_path, dur_sec, track_id):
    fname = sample_filename(str(track_id))
    out_path = SAMPLES_DIR / fname
    if out_path.exists():
        return fname

    start = max(15, int(dur_sec * SAMPLE_OFFSET_RATIO))
    if dur_sec > 30:
        start = min(start, int(dur_sec - 10))

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(SAMPLE_DURATION),
           "-i", src_path, "-c:a", "libmp3lame", "-b:a", "128k",
           "-af", "afade=t=in:d=0.01,afade=t=out:st=1.9:d=0.1", str(out_path)]
    try:
        subprocess.run(cmd, check=True, timeout=15)
        return fname
    except Exception:
        cmd[4] = "30"  # Fallback: fester Start bei 30s
        try:
            subprocess.run(cmd, check=True, timeout=15)
            return fname
        except Exception as e:
            print(f"    FAILED: {os.path.basename(src_path)} ({e})")
            return ""


# ── HTML bauen ───────────────────────────────────────────────────
def fmt_bpm(bpm):
    return str(int(bpm)) if bpm == int(bpm) else f"{bpm:.1f}"


def make_card(track, cover_map, sample_file):
    key = f"{track['title'].strip().lower()}|||{track['artist'].strip().lower()}"
    cover = cover_map.get(key, "")

    t = html_mod.escape(track["title"], quote=True)
    a = html_mod.escape(track["artist"], quote=True)
    p = html_mod.escape(track.get("playlist", ""), quote=True)

    card = f'<div class="card" onclick="toggleSong(this)" data-title="{t}" data-artist="{a}" data-playlist="{p}"'
    if sample_file:
        card += f' data-preview="samples/{sample_file}"'
    card += ">"

    if cover:
        img = f'<img src="{html_mod.escape(cover, quote=True)}" alt="Cover" class="cover" loading="lazy">'
        btn = PLAY_BTN if sample_file else ""
        cover_section = f'<div class="cover-wrap">{img}{btn}</div>'
    else:
        btn = PLAY_BTN if sample_file else ""
        cover_section = f'<div class="cover-wrap"><div class="cover no-cover">🎵</div>{btn}</div>'

    info = (f'<div class="card-info">\n'
            f'        <div class="card-title" title="{t}">{t}</div>\n'
            f'        <div class="card-artist">{a}</div>\n'
            f'        <div class="card-meta"><span class="badge bpm">{fmt_bpm(track["bpm"])}</span>'
            f'<span class="badge key">{track["key"]}</span><span class="badge dur">{track["duration"]}</span></div>\n'
            f'      </div>\n    </div>')

    return (f'{card}\n      <div class="card-check"><span class="check-icon">✕</span></div>\n'
            f'      {cover_section}\n      {info}')


def main():
    SAMPLES_DIR.mkdir(exist_ok=True)

    print("Parsing existing HTML for cover art...")
    cover_map, old_html = parse_existing_html()
    print(f"  {len(cover_map)} Cover-Einträge")

    print("Reading Rekordbox database...")
    playlists = read_rekordbox_data()
    total = sum(len(p["tracks"]) for p in playlists)
    print(f"  {total} Tracks in {len(playlists)} Playlists")

    print("\nGenerating audio samples...")
    sample_map = {}
    done = 0
    for pl in playlists:
        for track in pl["tracks"]:
            tid = track["id"]
            if tid in sample_map:
                continue
            if os.path.exists(track["path"]) and track["dur_sec"] > 0:
                fname = generate_sample(track["path"], track["dur_sec"], tid)
                sample_map[tid] = fname
                done += 1
                if done % 20 == 0:
                    print(f"  {done}/{total}...")
    print(f"  {done} Samples, {total - done} fehlende Dateien")

    print("\nBuilding HTML...")
    style = re.search(r'<style type="text/tailwindcss">(.*?)</style>', old_html, re.DOTALL)
    css = style.group(0) if style else ""
    js = re.search(r'</main>\s*(<script>.*?</script>.*?)</body>', old_html, re.DOTALL)
    js_block = js.group(1) if js else ""

    nav, sections = [], []
    for pl in playlists:
        sid = pl["name"].replace(" #", "-").replace(" ", "-").replace("&", "").replace("#", "-")
        nav.append(f'<a href="#{sid}">{html_mod.escape(pl["name"])}</a>')
        count = len(pl["tracks"])
        if count == 0:
            cards = '<div class="card" style="justify-content:center;color:#71717a;font-size:.8rem;padding:20px">Keine Songs in dieser Playlist</div>'
        else:
            cards = "".join(make_card({**t, "playlist": pl["name"]}, cover_map, sample_map.get(t["id"], "")) for t in pl["tracks"])
        sections.append(f'<section class="playlist-section" id="{sid}">\n      <h2>{html_mod.escape(pl["name"])} <span class="count">{count} Songs</span></h2>\n      <div class="cards">{cards}</div>\n    </section>')

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hochzeiten GardenCity — Playlists</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script>tailwind.config={{theme:{{extend:{{fontFamily:{{sans:['Inter','system-ui','sans-serif']}}}}}}}}</script>
{css}
</head>
<body>
<header>
  <h1>Hochzeiten GardenCity — Playlists</h1>
  <p>Rekordbox Library · {total} Songs in {len(playlists)} Playlists</p>
  <p class="hint">▶ Vorschau per Klick auf den Play-Button</p>
  <nav class="nav">{''.join(nav)}</nav>
</header>
<main>
{''.join(sections)}
</main>
{js_block}</body>
</html>"""

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    size = sum(os.path.getsize(SAMPLES_DIR / f) for f in os.listdir(SAMPLES_DIR) if f.endswith(".mp3"))
    print(f"\nDone! {done} Samples, {size / 1024 / 1024:.1f} MB total")
    print(f"Updated: {HTML_PATH}")
    print("\nNext: git add p/gardencity && git commit && git push")


if __name__ == "__main__":
    main()
