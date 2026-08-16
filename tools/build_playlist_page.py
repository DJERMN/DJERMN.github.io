#!/usr/bin/env python3
"""
Rekordbox-Playlist-Seite generisch aktualisieren.

Liest einen Rekordbox-Ordner (inkl. Subplaylists) aus der lokalen DB,
erzeugt 3s-MP3-Samples (Songmitte) aus den lokalen Files und baut die
HTML-Seite neu. Cover-Art der bestehenden Seite bleibt erhalten.

Usage:
  python3 tools/build_playlist_page.py --folder "Hochzeiten GardenCity" \
      --output p/gardencity/index.html

  python3 tools/build_playlist_page.py --folder "Hochzeiten" \
      --output p/0da1f091d91e/hochzeiten.html

Options:
  --folder   Name des Rekordbox-Ordners (Pflicht)
  --output   Ziel-HTML-Datei, relativ zum Repo (Pflicht)
  --title    Seiten-Titel (Default: "<Folder> — Playlists")
  --db       Pfad zur master.db (Default: Standard-Rekordbox-Pfad)
  --no-samples  Nur HTML bauen, keine Samples erzeugen

Requirements:
  pip3 install pyrekordbox sqlalchemy
  brew install ffmpeg
"""

import argparse
import hashlib
import html as html_mod
import os
import re
import subprocess
from pathlib import Path

from pyrekordbox.db6.database import Rekordbox6Database
from pyrekordbox.db6.tables import DjmdContent, DjmdSongPlaylist, DjmdPlaylist, DjmdKey
from sqlalchemy import select

REPO_DIR = Path(__file__).resolve().parent.parent  # .../DJERMN.github.io
DEFAULT_DB = "/Users/bariser/Library/Pioneer/rekordbox/master.db"

SAMPLE_DURATION = 3         # Sekunden
SAMPLE_OFFSET_RATIO = 0.5   # Start in der Songmitte

PLAY_BTN = ('<button class="play-btn" onclick="playPreview(event,this)" aria-label="Vorschau">'
            '<svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22">'
            '<path d="M8 5v14l11-7z"/></svg></button>')


# ── Rekordbox lesen ──────────────────────────────────────────────
def read_rekordbox_data(db_path: str, folder_name: str):
    db = Rekordbox6Database(db_path)

    folder = None
    for row in db.session.execute(select(DjmdPlaylist)).fetchall():
        if row[0].Name == folder_name and row[0].Attribute == 1:
            folder = row[0]
            break
    if folder is None:
        raise SystemExit(f"Ordner '{folder_name}' nicht in Rekordbox gefunden")

    # Alle Playlists (auch in Subordnern) rekursiv einsammeln
    def collect(node_id, playlists_out):
        pl_stmt = (select(DjmdPlaylist)
                   .where(DjmdPlaylist.ParentID == str(node_id))
                   .order_by(DjmdPlaylist.Seq))
        for row in db.session.execute(pl_stmt).fetchall():
            node = row[0]
            if node.Attribute == 1:
                collect(node.ID, playlists_out)  # Subordner
            else:
                playlists_out.append(node)
        return playlists_out

    playlist_rows = collect(folder.ID, [])

    key_cache = {}
    for row in db.session.execute(select(DjmdKey)).fetchall():
        key_cache[row[0].ID] = row[0].ScaleName

    playlists = []
    for pl in playlist_rows:
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
def parse_existing_html(html_path: Path):
    if not html_path.exists():
        return {}, ""
    with open(html_path, "r", encoding="utf-8") as f:
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
    return hashlib.md5(str(track_id).encode()).hexdigest()[:12] + ".mp3"


def generate_sample(src_path, dur_sec, track_id, out_dir: Path):
    fname = sample_filename(track_id)
    out_path = out_dir / fname
    if out_path.exists():
        return fname

    start = max(15, int(dur_sec * SAMPLE_OFFSET_RATIO))
    if dur_sec > 30:
        start = min(start, int(dur_sec - 10))

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(SAMPLE_DURATION),
           "-i", src_path, "-c:a", "libmp3lame", "-b:a", "128k",
           "-af", "afade=t=in:d=0.01,afade=t=out:st=2.9:d=0.1", str(out_path)]
    try:
        subprocess.run(cmd, check=True, timeout=15)
        return fname
    except Exception:
        cmd[5] = "30"  # Fallback: fester Start bei 30s
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


def build_html(playlists, cover_map, sample_map, title, old_html):
    total = sum(len(p["tracks"]) for p in playlists)

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
<title>{html_mod.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script>tailwind.config={{theme:{{extend:{{fontFamily:{{sans:['Inter','system-ui','sans-serif']}}}}}}}}</script>
{css}
</head>
<body>
<header>
  <h1>{html_mod.escape(title)}</h1>
  <p>Rekordbox Library · {total} Songs in {len(playlists)} Playlists</p>
  <p class="hint">▶ Vorschau per Klick auf den Play-Button</p>
  <nav class="nav">{''.join(nav)}</nav>
</header>
<main>
{''.join(sections)}
</main>
{js_block}</body>
</html>"""
    return html


def main():
    ap = argparse.ArgumentParser(description="Rekordbox-Playlist-Seite generisch bauen")
    ap.add_argument("--folder", required=True, help="Rekordbox-Ordner-Name")
    ap.add_argument("--output", required=True, help="Ziel-HTML relativ zum Repo-Root")
    ap.add_argument("--title", default=None, help="Seiten-Titel (Default: '<Folder> — Playlists')")
    ap.add_argument("--db", default=DEFAULT_DB, help="Pfad zur Rekordbox master.db")
    ap.add_argument("--no-samples", action="store_true", help="Keine Samples erzeugen")
    args = ap.parse_args()

    title = args.title or f"{args.folder} — Playlists"
    html_path = (REPO_DIR / args.output).resolve()
    samples_dir = html_path.parent / "samples"
    samples_dir.mkdir(exist_ok=True)

    print(f"Folder:    {args.folder}")
    print(f"Output:    {html_path.relative_to(REPO_DIR)}")
    print(f"Title:     {title}")

    print("\nParsing existing HTML for cover art...")
    cover_map, old_html = parse_existing_html(html_path)
    print(f"  {len(cover_map)} Cover-Einträge")

    print("Reading Rekordbox database...")
    playlists = read_rekordbox_data(args.db, args.folder)
    total = sum(len(p["tracks"]) for p in playlists)
    print(f"  {total} Tracks in {len(playlists)} Playlists")

    sample_map = {}
    if args.no_samples:
        print("\nSkipping samples (--no-samples)")
    else:
        print("\nGenerating audio samples...")
        done = 0
        for pl in playlists:
            for track in pl["tracks"]:
                tid = track["id"]
                if tid in sample_map:
                    continue
                if os.path.exists(track["path"]) and track["dur_sec"] > 0:
                    fname = generate_sample(track["path"], track["dur_sec"], tid, samples_dir)
                    sample_map[tid] = fname
                    done += 1
                    if done % 20 == 0:
                        print(f"  {done}/{total}...")
        print(f"  {done} Samples, {total - done} fehlende Dateien")

    print("\nBuilding HTML...")
    html = build_html(playlists, cover_map, sample_map, title, old_html)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    size = sum(os.path.getsize(samples_dir / f) for f in os.listdir(samples_dir) if f.endswith(".mp3"))
    print(f"\nDone! Samples: {size / 1024 / 1024:.1f} MB total")
    print(f"Updated: {html_path.relative_to(REPO_DIR)}")
    print("\nNext: git add && git commit && git push")


if __name__ == "__main__":
    main()
