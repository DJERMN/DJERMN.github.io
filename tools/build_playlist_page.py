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

PLAY_ALL_STYLE = """<style>
.play-all-btn { display:inline-flex; align-items:center; gap:6px; margin-left:auto; padding:5px 10px; border:1px solid rgba(251,191,36,.28); border-radius:999px; background:rgba(245,158,11,.08); color:#fbbf24; font:inherit; font-size:.68rem; line-height:1.2; white-space:nowrap; cursor:pointer; transition:background .15s,border-color .15s,color .15s; -webkit-tap-highlight-color:transparent; touch-action:manipulation; }
.play-all-btn:hover { background:rgba(245,158,11,.16); border-color:rgba(251,191,36,.5); }
.play-all-btn.is-playing { background:rgba(245,158,11,.2); border-color:rgba(251,191,36,.65); color:#fde68a; }
.play-all-btn:disabled { opacity:.4; cursor:not-allowed; }
.play-all-icon { width:10px; font-size:.65rem; }
.feedback-status { margin-top:5px; font-size:.75rem; line-height:1.35; }
.feedback-status.success { color:#86efac; }
.feedback-status.error { color:#fca5a5; }
.btn-whatsapp { padding:7px 10px; border:0; border-radius:8px; background:#25d366; color:#052e16; font:inherit; font-size:.75rem; font-weight:700; cursor:pointer; }
</style>"""

PLAY_ALL_SCRIPT = """<script>
  const PLAYLIST_CROSSFADE_SECONDS = 0.45;
  let _playlistPlayback = null;

  function _setPlaylistButton(button, playing, labelText) {
    if (!button) return;
    button.classList.toggle('is-playing', playing);
    const icon = button.querySelector('.play-all-icon');
    const label = button.querySelector('.play-all-label');
    if (icon) icon.textContent = playing ? '■' : '▶';
    if (label) label.textContent = labelText || (playing ? 'Stop' : 'Play All');
    button.setAttribute('aria-pressed', String(playing));
    button.setAttribute('aria-label', playing ? 'Wiedergabe stoppen' : button.dataset.playLabel);
  }

  function stopPlaylistPlayback() {
    const state = _playlistPlayback;
    if (!state) return;
    _playlistPlayback = null;
    state.sources.forEach(source => {
      source.onended = null;
      try { source.stop(); } catch (error) {}
    });
    if (state.context && state.context.state !== 'closed') state.context.close().catch(() => {});
    _setPlaylistButton(state.button, false);
  }

  function togglePlaylistPlayback(event, button) {
    event.stopPropagation();
    if (_playlistPlayback && _playlistPlayback.button === button) {
      stopPlaylistPlayback();
      return;
    }
    stopPlaylistPlayback();
    if (typeof _stop === 'function') _stop();

    const section = button.closest('.playlist-section');
    const cards = Array.from(section.querySelectorAll('.card[data-preview]'));
    if (!cards.length) return;

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      _showPlaylistError();
      return;
    }

    const state = { button, cards, context: new AudioContextClass(), sources: [] };
    _playlistPlayback = state;
    _setPlaylistButton(button, true, 'Lädt…');
    _loadAndStartPlaylist(state);
  }

  function _showPlaylistError() {
    const toast = document.getElementById('toast');
    if (!toast || typeof showToast !== 'function') return;
    toast.textContent = 'Vorschau konnte nicht geladen werden.';
    showToast();
  }

  async function _loadAndStartPlaylist(state) {
    try {
      // Resume inside the click gesture so mobile browsers allow scheduled playback.
      await state.context.resume();
      const buffers = await Promise.all(state.cards.map(async card => {
        try {
          const response = await fetch(card.dataset.preview);
          if (!response.ok) return null;
          return await state.context.decodeAudioData(await response.arrayBuffer());
        } catch (error) {
          return null;
        }
      }));

      if (_playlistPlayback !== state) return;
      const playable = buffers.filter(Boolean);
      if (!playable.length) {
        stopPlaylistPlayback();
        _showPlaylistError();
        return;
      }
      _schedulePlaylist(state, playable);
      _setPlaylistButton(state.button, true);
    } catch (error) {
      if (_playlistPlayback === state) {
        stopPlaylistPlayback();
        _showPlaylistError();
      }
    }
  }

  function _schedulePlaylist(state, buffers) {
    const context = state.context;
    let startAt = context.currentTime + 0.08;
    let previousFade = 0;

    buffers.forEach((buffer, index) => {
      const source = context.createBufferSource();
      const gain = context.createGain();
      source.buffer = buffer;
      source.connect(gain);
      gain.connect(context.destination);

      const duration = buffer.duration;
      const nextBuffer = buffers[index + 1];
      const fadeOut = nextBuffer
        ? Math.min(PLAYLIST_CROSSFADE_SECONDS, duration * 0.45, nextBuffer.duration * 0.45)
        : Math.min(0.2, duration * 0.25);
      const fadeIn = index === 0 ? Math.min(0.12, duration * 0.25) : previousFade;

      gain.gain.setValueAtTime(0, startAt);
      gain.gain.linearRampToValueAtTime(1, startAt + fadeIn);
      gain.gain.setValueAtTime(1, startAt + duration - fadeOut);
      gain.gain.linearRampToValueAtTime(0, startAt + duration);
      source.start(startAt);
      state.sources.push(source);

      if (index === buffers.length - 1) {
        source.onended = () => {
          if (_playlistPlayback === state) stopPlaylistPlayback();
        };
      }

      startAt += duration - fadeOut;
      previousFade = fadeOut;
    });
  }

  document.querySelectorAll('.card .play-btn').forEach(button =>
    button.addEventListener('click', stopPlaylistPlayback, true));
</script>"""

PLAYLIST_FEEDBACK_SCRIPT = """<script>
  const playlistFeedbackEndpoint = document.querySelector('meta[name="playlist-feedback-endpoint"]')?.content.trim() || '';
  const playlistFeedbackButton = document.querySelector('.btn-share');

  if (playlistFeedbackEndpoint && playlistFeedbackButton) {
    const responseFrame = document.createElement('iframe');
    responseFrame.name = 'playlist-feedback-response';
    responseFrame.title = 'Antwort der Formularübermittlung';
    responseFrame.hidden = true;
    responseFrame.style.display = 'none';
    document.body.appendChild(responseFrame);

    const honeypot = document.createElement('input');
    honeypot.type = 'text';
    honeypot.name = 'website';
    honeypot.autocomplete = 'off';
    honeypot.tabIndex = -1;
    honeypot.setAttribute('aria-hidden', 'true');
    honeypot.style.cssText = 'position:fixed;left:-10000px;top:auto;width:1px;height:1px;opacity:0';
    document.body.appendChild(honeypot);

    const status = document.createElement('div');
    status.className = 'feedback-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    document.querySelector('.share-bar-info').appendChild(status);

    const whatsappButton = document.createElement('button');
    whatsappButton.type = 'button';
    whatsappButton.className = 'btn-whatsapp';
    whatsappButton.textContent = 'Manuell via WhatsApp teilen';
    whatsappButton.hidden = true;
    document.querySelector('.share-actions').appendChild(whatsappButton);

    let activeSubmissionId = '';
    let responseTimeout = null;

    playlistFeedbackButton.removeAttribute('onclick');
    playlistFeedbackButton.textContent = 'Absenden';
    playlistFeedbackButton.setAttribute('aria-label', 'Ausschlussliste absenden');
    playlistFeedbackButton.onclick = submitPlaylistFeedback;

    function setFeedbackStatus(message, type) {
      status.textContent = message;
      status.className = 'feedback-status ' + type;
    }

    function selectedSongs() {
      return Array.from(document.querySelectorAll('.card.selected')).map(card => ({
        song: card.dataset.title,
        artist: card.dataset.artist,
        playlist: card.dataset.playlist
      }));
    }

    function openWhatsApp() {
      const songs = selectedSongs();
      const grouped = {};
      songs.forEach(song => {
        if (!grouped[song.playlist]) grouped[song.playlist] = [];
        grouped[song.playlist].push('• ' + song.song + ' — ' + song.artist);
      });
      const lines = [
        document.querySelector('h1')?.textContent.trim() || document.title,
        '',
        'Diese Songs bitte weglassen:',
        ...Object.entries(grouped).flatMap(([playlist, entries]) => [playlist + ':', ...entries, ''])
      ];
      window.open('https://wa.me/?text=' + encodeURIComponent(lines.join('\\n')), '_blank', 'noopener,noreferrer');
    }

    function finishSubmission(result) {
      if (responseTimeout) clearTimeout(responseTimeout);
      responseTimeout = null;
      activeSubmissionId = '';
      playlistFeedbackButton.disabled = false;
      if (result.ok) {
        setFeedbackStatus('Datenübertragung erfolgreich!', 'success');
        whatsappButton.hidden = true;
        playlistFeedbackButton.disabled = true;
        playlistFeedbackButton.textContent = 'Gesendet';
      } else {
        setFeedbackStatus('Übermittlung hat leider nicht stattgefunden, teilen Sie manuell via WhatsApp.', 'error');
        whatsappButton.hidden = false;
        playlistFeedbackButton.textContent = 'Erneut versuchen';
      }
    }

    window.addEventListener('message', event => {
      const result = event.data;
      // Apps Script can wrap the response in a sandbox frame, so correlate by the
      // per-submission nonce rather than requiring one exact WindowProxy source.
      if (!result || result.type !== 'playlist-feedback-result' || result.submissionId !== activeSubmissionId) return;
      finishSubmission(result);
    });

    whatsappButton.addEventListener('click', openWhatsApp);
    document.querySelectorAll('.card').forEach(card => card.addEventListener('click', () => {
      if (playlistFeedbackButton.textContent !== 'Wird gesendet…') {
        playlistFeedbackButton.disabled = false;
        playlistFeedbackButton.textContent = 'Absenden';
      }
      status.textContent = '';
      whatsappButton.hidden = true;
    }, true));

    function submitPlaylistFeedback() {
      const songs = selectedSongs();
      if (!songs.length || activeSubmissionId) return;

      activeSubmissionId = (crypto.randomUUID && crypto.randomUUID()) ||
        (Date.now().toString(36) + Math.random().toString(36).slice(2));
      const payload = {
        submissionId: activeSubmissionId,
        origin: window.location.origin,
        event: document.querySelector('h1')?.textContent.trim() || document.title,
        website: honeypot.value,
        songs
      };

      playlistFeedbackButton.disabled = true;
      playlistFeedbackButton.textContent = 'Wird gesendet…';
      status.textContent = '';
      whatsappButton.hidden = true;

      const form = document.createElement('form');
      form.method = 'POST';
      form.action = playlistFeedbackEndpoint;
      form.target = responseFrame.name;
      form.style.display = 'none';
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'payload';
      input.value = JSON.stringify(payload);
      form.appendChild(input);
      document.body.appendChild(form);
      responseTimeout = setTimeout(() => finishSubmission({ ok: false }), 20000);
      try {
        form.submit();
        setTimeout(() => form.remove(), 0);
      } catch (error) {
        form.remove();
        finishSubmission({ ok: false });
      }
    }
  }
</script>"""


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
            title = content.Title or "Unknown"
            if re.fullmatch(r"[\s\-–—]+", title):
                continue
            dur = content.Length or 0
            tracks.append({
                "id": content.ID,
                "title": title,
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


def build_html(playlists, cover_map, sample_map, title, old_html, feedback_endpoint=""):
    total = sum(len(p["tracks"]) for p in playlists)

    style = re.search(r'<style type="text/tailwindcss">(.*?)</style>', old_html, re.DOTALL)
    css = style.group(0) if style else ""
    js = re.search(r'</main>\s*(.*?)</body>', old_html, re.DOTALL)
    tail_block = js.group(1) if js else ""
    tail_block = re.sub(r'<script>\s*const PLAYLIST_CROSSFADE_SECONDS\b.*?</script>', "", tail_block, flags=re.DOTALL)
    tail_block = re.sub(r'<script>\s*const playlistFeedbackEndpoint\b.*?</script>', "", tail_block, flags=re.DOTALL)
    if not feedback_endpoint:
        endpoint = re.search(r'<meta name="playlist-feedback-endpoint" content="([^"]*)"', old_html)
        if endpoint:
            feedback_endpoint = html_mod.unescape(endpoint.group(1))

    nav, sections = [], []
    for pl in playlists:
        sid = pl["name"].replace(" #", "-").replace(" ", "-").replace("&", "").replace("#", "-")
        nav.append(f'<a href="#{sid}">{html_mod.escape(pl["name"])}</a>')
        count = len(pl["tracks"])
        if count == 0:
            cards = '<div class="card" style="justify-content:center;color:#71717a;font-size:.8rem;padding:20px">Keine Songs in dieser Playlist</div>'
        else:
            cards = "".join(make_card({**t, "playlist": pl["name"]}, cover_map, sample_map.get(t["id"], "")) for t in pl["tracks"])
        preview_count = sum(bool(sample_map.get(t["id"])) for t in pl["tracks"])
        disabled = " disabled" if preview_count == 0 else ""
        playlist_name = html_mod.escape(pl["name"])
        playlist_name_attr = html_mod.escape(pl["name"], quote=True)
        play_all_button = (
            f'<button class="play-all-btn" type="button" onclick="togglePlaylistPlayback(event,this)"'
            f' aria-label="Play All: {playlist_name_attr}" aria-pressed="false"'
            f' data-play-label="Play All: {playlist_name_attr}"{disabled}>'
            f'<span class="play-all-icon">▶</span><span class="play-all-label">Play All</span></button>'
        )
        sections.append(f'<section class="playlist-section" id="{sid}">\n      <h2>{playlist_name} <span class="count">{count} Songs</span>{play_all_button}</h2>\n      <div class="cards">{cards}</div>\n    </section>')

    play_all_script = PLAY_ALL_SCRIPT
    feedback_script = PLAYLIST_FEEDBACK_SCRIPT

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="playlist-feedback-endpoint" content="{html_mod.escape(feedback_endpoint, quote=True)}">
<title>{html_mod.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<script>tailwind.config={{theme:{{extend:{{fontFamily:{{sans:['Inter','system-ui','sans-serif']}}}}}}}}</script>
{css}
{PLAY_ALL_STYLE}
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
{tail_block}
{play_all_script}
{feedback_script}</body>
</html>"""
    return html


def main():
    ap = argparse.ArgumentParser(description="Rekordbox-Playlist-Seite generisch bauen")
    ap.add_argument("--folder", required=True, help="Rekordbox-Ordner-Name")
    ap.add_argument("--output", required=True, help="Ziel-HTML relativ zum Repo-Root")
    ap.add_argument("--title", default=None, help="Seiten-Titel (Default: '<Folder> — Playlists')")
    ap.add_argument("--db", default=DEFAULT_DB, help="Pfad zur Rekordbox master.db")
    ap.add_argument("--no-samples", action="store_true", help="Keine Samples erzeugen")
    ap.add_argument("--feedback-endpoint", default=os.environ.get("PLAYLIST_FEEDBACK_ENDPOINT", ""),
                    help="Google Apps Script Web-App URL for playlist feedback")
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
    html = build_html(playlists, cover_map, sample_map, title, old_html, args.feedback_endpoint)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    size = sum(os.path.getsize(samples_dir / f) for f in os.listdir(samples_dir) if f.endswith(".mp3"))
    print(f"\nDone! Samples: {size / 1024 / 1024:.1f} MB total")
    print(f"Updated: {html_path.relative_to(REPO_DIR)}")
    print("\nNext: git add && git commit && git push")


if __name__ == "__main__":
    main()
