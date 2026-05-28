#!/usr/bin/env python3
"""
thb_punct_extractor.py

Reads OSHB MorphHB XML files (same source as thb.1.3.mt.json) and
extracts punctuation elements that were skipped during original ingestion:

  <seg type="x-paseq">   → has_paseq: true on the preceding word token
  <seg type="x-pe">      → parashah: "pe"   on the verse object
  <seg type="x-samekh">  → parashah: "samekh" on the verse object

Maqaf (x-maqqef) and sof pasuq (x-sof-pasuq) are already handled.

Matching strategy: positional — walk XML <w> elements and JSON word
tokens in lockstep within each verse (same source, same order).
The JSON sof-pasuq sentinel token has no <w> counterpart so it is
skipped during matching.

Output: thb/backend/thb.1.5.mt.json

Usage:
    python thb/thb_punct_extractor.py
    python thb/thb_punct_extractor.py --book Gen
    python thb/thb_punct_extractor.py --no-fetch   # cached XML only
    python thb/thb_punct_extractor.py --dry-run
"""

import argparse
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding='utf-8')

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RAW_BASE  = 'https://raw.githubusercontent.com/openscriptures/morphhb/master/wlc/{}.xml'
CACHE_DIR = Path('thb/backend/oshb_cache')
INPUT_MT  = Path('thb/backend/thb.1.3.mt.json')
OUTPUT_MT = Path('thb/backend/thb.1.5.mt.json')

OSIS_NS = 'http://www.bibletechnologies.net/2003/OSIS/namespace'

# OSHB filename → our internal book name
BOOK_MAP: Dict[str, str] = {
    'Gen':   'Genesis',
    'Exod':  'Exodus',
    'Lev':   'Leviticus',
    'Num':   'Numbers',
    'Deut':  'Deuteronomy',
    'Josh':  'Joshua',
    'Judg':  'Judges',
    'Ruth':  'Ruth',
    '1Sam':  '1 Samuel',
    '2Sam':  '2 Samuel',
    '1Kgs':  '1 Kings',
    '2Kgs':  '2 Kings',
    '1Chr':  '1 Chronicles',
    '2Chr':  '2 Chronicles',
    'Ezra':  'Ezra',
    'Neh':   'Nehemiah',
    'Esth':  'Esther',
    'Job':   'Job',
    'Ps':    'Psalms',
    'Prov':  'Proverbs',
    'Eccl':  'Ecclesiastes',
    'Song':  'Song of Solomon',
    'Isa':   'Isaiah',
    'Jer':   'Jeremiah',
    'Lam':   'Lamentations',
    'Ezek':  'Ezekiel',
    'Dan':   'Daniel',
    'Hos':   'Hosea',
    'Joel':  'Joel',
    'Amos':  'Amos',
    'Obad':  'Obadiah',
    'Jonah': 'Jonah',
    'Mic':   'Micah',
    'Nah':   'Nahum',
    'Hab':   'Habakkuk',
    'Zeph':  'Zephaniah',
    'Hag':   'Haggai',
    'Zech':  'Zechariah',
    'Mal':   'Malachi',
}

# ---------------------------------------------------------------------------
# Fetch / cache
# ---------------------------------------------------------------------------

def fetch_xml(oshb_name: str, network_ok: bool = True) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f'{oshb_name}.xml'
    if cache.exists():
        print(f'  (cache) {oshb_name}.xml')
        return cache.read_text(encoding='utf-8')
    if not network_ok:
        print(f'  SKIP {oshb_name} — no cache and --no-fetch set')
        return ''
    url = RAW_BASE.format(oshb_name)
    print(f'  Fetching {url} ...', end=' ', flush=True)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'THB/1.5'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode('utf-8')
        cache.write_text(data, encoding='utf-8')
        print('OK')
        time.sleep(0.3)
        return data
    except Exception as e:
        print(f'FAILED: {e}')
        return ''

# ---------------------------------------------------------------------------
# XML parsing — extract paseq and parashah positions per verse
# ---------------------------------------------------------------------------

def parse_oshb_book(xml_text: str) -> Dict[Tuple[int,int], Dict]:
    """
    Returns {(chapter, verse): {
        'paseq_after':        [word_index, ...],   # 0-based; paseq follows w[i]
        'reversed_nun_after': [word_index, ...],   # nun inversum follows w[i]
        'parashah':           None | 'pe' | 'samekh',
        'large_letter':       [word_index, ...],   # w[i] contains a large letter
        'small_letter':       [word_index, ...],   # w[i] contains a small letter
        'suspended_letter':   [word_index, ...],   # w[i] contains a suspended letter
    }}
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f'    XML parse error: {e}')
        return {}

    def tag(elem):
        t = elem.tag
        return t.split('}', 1)[1] if '}' in t else t

    result: Dict[Tuple[int,int], Dict] = {}

    # Pre-build a set of <w> elements nested inside <note> (ketiv/qere alternates).
    # root.iter() visits them, but they have no JSON counterpart — skip them.
    note_w_set: set = set()
    for note_elem in root.iter():
        if tag(note_elem) == 'note':
            for child in note_elem.iter():
                if tag(child) == 'w':
                    note_w_set.add(id(child))

    current_verse: Optional[Tuple[int,int]] = None
    # json_idx tracks the JSON token index (which may exceed xml word count when
    # a single <w> contains morph splits like "HC/Vqq2ms" → 2 JSON tokens).
    json_idx = 0
    paseq_after: List[int] = []
    reversed_nun_after: List[int] = []
    parashah: Optional[str] = None
    large_letter: List[int] = []
    small_letter: List[int] = []
    suspended_letter: List[int] = []
    last_json_last: int = -1  # json index of the LAST token of the most recent <w>

    def flush():
        nonlocal current_verse, json_idx, paseq_after, reversed_nun_after
        nonlocal parashah, large_letter, small_letter, suspended_letter, last_json_last
        if current_verse is not None:
            result[current_verse] = {
                'paseq_after':        paseq_after,
                'reversed_nun_after': reversed_nun_after,
                'parashah':           parashah,
                'large_letter':       large_letter,
                'small_letter':       small_letter,
                'suspended_letter':   suspended_letter,
            }
        current_verse      = None
        json_idx           = 0
        paseq_after        = []
        reversed_nun_after = []
        parashah           = None
        large_letter       = []
        small_letter       = []
        suspended_letter   = []
        last_json_last     = -1

    for elem in root.iter():
        t = tag(elem)

        if t == 'verse':
            sid = elem.get('sID') or elem.get('osisID')
            eid = elem.get('eID')
            if eid:
                flush()
                continue
            if sid:
                flush()
                parts = sid.split('.')
                try:
                    ch = int(parts[-2])
                    vn = int(parts[-1])
                    current_verse = (ch, vn)
                except (ValueError, IndexError):
                    current_verse = None
            continue

        if current_verse is None:
            continue

        if t == 'w':
            # Skip <w> elements inside <note> (ketiv/qere alternates, commentary)
            if id(elem) in note_w_set:
                continue
            # Each '/' in the morph code represents one additional JSON token
            # (prefix or suffix split). e.g. "HC/Vqq2ms" → 2 tokens.
            morph = elem.get('morph', '')
            extra = morph.count('/')
            first_tok = json_idx          # intra-word marks go on the first token
            last_tok  = json_idx + extra  # post-word marks go on the last token

            # Check children for intra-word scribal marks (large, small, suspended)
            for child in elem:
                ct = tag(child)
                if ct == 'seg':
                    ctype = child.get('type', '')
                    if ctype == 'x-large':
                        large_letter.append(first_tok)
                    elif ctype == 'x-small':
                        small_letter.append(first_tok)
                    elif ctype == 'x-suspended':
                        suspended_letter.append(first_tok)

            last_json_last = last_tok
            json_idx = last_tok + 1

        elif t == 'seg':
            # Top-level (between-word) marks — intra-word segs are handled above
            # and silently ignored here since their types aren't in the list below.
            seg_type = elem.get('type', '')
            if seg_type == 'x-paseq' and last_json_last >= 0:
                paseq_after.append(last_json_last)
            elif seg_type == 'x-reversednun':
                reversed_nun_after.append(last_json_last if last_json_last >= 0 else 0)
            elif seg_type == 'x-pe':
                parashah = 'pe'
            elif seg_type == 'x-samekh':
                parashah = 'samekh'
            # x-maqqef and x-sof-pasuq already handled in thb.1.3

    flush()
    return result


# ---------------------------------------------------------------------------
# Integration — apply to one book's JSON chapters
# ---------------------------------------------------------------------------

def integrate_book(
    book_name: str,
    oshb_chapters: List[Dict],
    punct_data: Dict[Tuple[int,int], Dict],
) -> Tuple[List[Dict], Dict]:

    stats = {
        'verses':              0,
        'paseq_added':         0,
        'reversed_nun_added':  0,
        'parashah_added':      0,
        'large_letter_added':  0,
        'small_letter_added':  0,
        'suspended_added':     0,
        'mismatches':          0,
    }

    new_chapters = []
    for ch_obj in oshb_chapters:
        ch_num = ch_obj['chapter']
        new_verses = []

        for v_obj in ch_obj['verses']:
            vn = v_obj['verse']
            stats['verses'] += 1

            key = (ch_num, vn)
            punct = punct_data.get(key)

            content_toks = [w for w in v_obj['words'] if not w.get('is_sof_pasuq')]
            sof_toks     = [w for w in v_obj['words'] if w.get('is_sof_pasuq')]

            new_v = dict(v_obj)

            if punct is None:
                new_verses.append(new_v)
                continue

            # Determine if we need to rebuild the word list at all
            word_flags: Dict[int, Dict] = {}  # idx → {flag: True, ...}

            def mark(idx, flag):
                if idx < len(content_toks):
                    word_flags.setdefault(idx, {})[flag] = True

            # Validate max index doesn't exceed word count
            all_indices = (
                punct['paseq_after'] +
                punct['reversed_nun_after'] +
                punct['large_letter'] +
                punct['small_letter'] +
                punct['suspended_letter']
            )
            if all_indices and max(all_indices) >= len(content_toks):
                stats['mismatches'] += 1
                # Still apply what we can (indices within range)

            for idx in punct['paseq_after']:
                mark(idx, 'has_paseq')
                stats['paseq_added'] += 1
            for idx in punct['reversed_nun_after']:
                mark(idx, 'has_reversed_nun')
                stats['reversed_nun_added'] += 1
            for idx in punct['large_letter']:
                mark(idx, 'has_large_letter')
                stats['large_letter_added'] += 1
            for idx in punct['small_letter']:
                mark(idx, 'has_small_letter')
                stats['small_letter_added'] += 1
            for idx in punct['suspended_letter']:
                mark(idx, 'has_suspended_letter')
                stats['suspended_added'] += 1

            if word_flags:
                new_words = [dict(w) for w in content_toks]
                for idx, flags in word_flags.items():
                    new_words[idx].update(flags)
                new_v['words'] = new_words + sof_toks

            # --- Parashah ---
            if punct['parashah']:
                new_v['parashah'] = punct['parashah']
                stats['parashah_added'] += 1

            new_verses.append(new_v)

        new_ch = dict(ch_obj)
        new_ch['verses'] = new_verses
        new_chapters.append(new_ch)

    return new_chapters, stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--book',     help='Process one book by OSHB code, e.g. Gen')
    parser.add_argument('--no-fetch', action='store_true')
    parser.add_argument('--dry-run',  action='store_true')
    args = parser.parse_args()

    print(f'Loading {INPUT_MT} ...')
    with open(INPUT_MT, encoding='utf-8') as f:
        mt_data: Dict = json.load(f)

    books = [args.book] if args.book else list(BOOK_MAP.keys())
    new_mt = dict(mt_data)

    totals = {'verses': 0, 'paseq_added': 0, 'reversed_nun_added': 0,
              'parashah_added': 0, 'large_letter_added': 0,
              'small_letter_added': 0, 'suspended_added': 0, 'mismatches': 0}

    for oshb_name in books:
        book_name = BOOK_MAP.get(oshb_name)
        if not book_name:
            print(f'[{oshb_name}] Unknown — skipping')
            continue
        if book_name not in mt_data:
            print(f'[{oshb_name}] Not in MT JSON — skipping')
            continue

        print(f'\n[{oshb_name} → {book_name}]')
        xml_text = fetch_xml(oshb_name, network_ok=not args.no_fetch)
        if not xml_text:
            continue

        punct_data = parse_oshb_book(xml_text)
        updated, stats = integrate_book(book_name, mt_data[book_name], punct_data)

        print(
            f'  verses={stats["verses"]}  '
            f'paseq={stats["paseq_added"]}  '
            f'parashah={stats["parashah_added"]}  '
            f'mismatches={stats["mismatches"]}'
        )

        for k in totals:
            totals[k] += stats[k]

        if not args.dry_run:
            new_mt[book_name] = updated

    print('\n=== TOTALS ===')
    for k, v in totals.items():
        print(f'  {k}: {v}')

    if not args.dry_run:
        print(f'\nWriting {OUTPUT_MT} ...')
        with open(OUTPUT_MT, 'w', encoding='utf-8') as f:
            json.dump(new_mt, f, ensure_ascii=False, separators=(',', ':'))
        print('Done.')
    else:
        print('\n(dry-run — no files written)')


if __name__ == '__main__':
    main()
