#!/usr/bin/env python3
"""
DJ-Library: Rekordbox-Collection nach Firebase syncen.

Liest alle Tracks aus der lokalen Rekordbox-DB (master.db),
baut daraus die Library (artist/title/genre/bpm/key/rating) und
ersetzt das Array in Firebase unter library/tracks.

Usage:
  python3 tools/sync_library.py [--dry-run] [--db /path/to/master.db]

Options:
  --dry-run   Nur Daten auslesen und Statistik zeigen, nichts pushen
  --db        Pfad zur master.db (Default: Standard-Rekordbox-Pfad)
  --limit     Maximal N Tracks pushen (Debug)

Requirements:
  pip3 install pyrekordbox sqlalchemy
"""

import argparse
import json
import os
import urllib.request
from collections import Counter

from pyrekordbox.db6.database import Rekordbox6Database
from sqlalchemy import text

FIREBASE_BASE = 'https://now-playing-75593-default-rtdb.europe-west1.firebasedatabase.app'
FIREBASE_URL = FIREBASE_BASE + '/library/tracks.json'

DEFAULT_DB = '/Users/bariser/Library/Pioneer/rekordbox/master.db'
SECRET_PATH = os.path.expanduser('~/Library/Application Support/djermn/firebase_secret.txt')

EXCLUDE_GENRES = {'Loop Samples'}

GENRE_MAP = {
    'Dance': [
        'Dance Commercial/Mainstream Club', 'Dance/Mainstream Club',
        'Electro', 'Electro House', 'Electro/Progressive',
        'Indie Dance / Nu Disco', 'Big Room', 'Big Room/EDM',
        'Trance', 'Future Bass',
    ],
    'Hip Hop': ['Hip Hop/Rap', 'Garage / Bassline / Grime', 'Trap / Future Bass'],
    'Latin': ['Latin / Reggaeton'],
    'House': [
        'Bass House', 'Classic House', 'Deep House',
        'Funky / Groove / Jackin\' House', 'House/Vocal House',
        'House/Vocal House/Bass House', 'House/Big Room',
        'Progressive House', 'Melodic House & Techno',
    ],
    'Techno': ['Techno (Peak Time)', 'Hard Dance / Hardcore'],
    'R&B': ['Hip-Hop / R&B'],
    'African': ['Afro House'],
    'Afrobeats': ['Afrobeats / Amapiano'],
    'Pop': ['Rock', 'Rock/Alternative', 'Country'],
}
MAIN_GENRES = {'Dance', 'Hip Hop', 'Latin', 'House', 'Pop', 'Club-Banger',
               'Drum & Bass', 'Tech House', 'R&B', 'Techno', 'African',
               'Reggae', 'Afrobeats'}
GENRE_MAP = {sub: main for main, subs in GENRE_MAP.items() for sub in subs}


def map_genre(genre: str) -> str:
    if not genre:
        return ''
    return GENRE_MAP.get(genre, genre)


def read_tracks(db_path: str):
    db = Rekordbox6Database(db_path, unlock=True)
    rows = db.session.execute(text("""
        SELECT c.ID, c.Title, a.Name AS artist, g.Name AS genre,
               c.BPM, k.ScaleName AS key, c.Rating
        FROM djmdContent c
        LEFT JOIN djmdArtist a ON c.ArtistID = a.ID
        LEFT JOIN djmdGenre g ON c.GenreID = g.ID
        LEFT JOIN djmdKey k ON c.KeyID = k.ID
        WHERE COALESCE(c.rb_local_deleted, 0) = 0
    """)).fetchall()
    db.close()

    tracks = []
    for r in rows:
        genre = (r[3] or '').strip()
        if genre in EXCLUDE_GENRES:
            continue
        tracks.append({
            'artist': (r[2] or '').strip(),
            'title':  (r[1] or '').strip(),
            'genre':  map_genre(genre),
            'bpm':    float(r[4]) if r[4] is not None else None,
            'key':    (r[5] or '').strip(),
            'rating': int(r[6] or 0),
        })
    return tracks


def sort_tracks(tracks):
    return sorted(tracks, key=lambda t: (
        t['artist'].lower(), t['title'].lower()))


def push_to_firebase(tracks):
    secret = open(SECRET_PATH).read().strip()
    url = FIREBASE_URL + '?auth=' + secret
    req = urllib.request.Request(
        url,
        data=json.dumps(tracks, ensure_ascii=False).encode(),
        method='PUT',
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'},
    )
    urllib.request.urlopen(req, timeout=60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    tracks = read_tracks(args.db)
    tracks = sort_tracks(tracks)
    if args.limit:
        tracks = tracks[:args.limit]

    genres = Counter(t['genre'] for t in tracks)
    with_key = sum(1 for t in tracks if t['key'])
    print(f'Tracks:      {len(tracks)}')
    print(f'Mit Key:     {with_key}')
    print('Genres:')
    for g, n in genres.most_common():
        print(f'  {g or "(ohne)":40s} {n}')

    if args.dry_run:
        print('\n[dry-run] nichts gepusht.')
        return

    push_to_firebase(tracks)
    print(f'\nPushed {len(tracks)} Tracks nach Firebase.')


if __name__ == '__main__':
    main()
