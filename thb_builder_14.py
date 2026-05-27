#!/usr/bin/env python3
"""
THB Site Builder - Complete Fixed Version
Generates static HTML pages from THB JSON data with all bug fixes applied.
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from urllib.parse import quote as urlquote

def normalize_for_search(text: str, tradition: str) -> str:
    """Strip all diacritics and noise — makes search effectively consonantal.

    NFD decomposition + combining-mark removal handles Hebrew nikud,
    Greek breathing/accent, and Latin accent in one pass.
    DSS/SP/KJV bracket noise is stripped separately.
    """
    if not text:
        return ''
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    if tradition in ('dss', 'sp', 'kjv'):
        text = re.sub(r'[\[\]{}#?·×%]', '', text)
    return text.strip()


_FINAL_TO_MEDIAL = str.maketrans('םןךףץ', 'מנכפצ')

def _strip_nikkud(text: str) -> str:
    """Reduce Hebrew text to bare consonants for SP lemma matching.

    Steps:
    1. NFD-decompose so precomposed forms (e.g. U+FB2A shin-with-dot) separate.
    2. Drop all combining characters (Hebrew points, cantillation, shin/sin dots,
       general diacritics).
    3. Map final letters (ם ן ך ף ץ) → medial equivalents (מ נ כ פ צ), because
       the Samaritan script has no final letter forms — SP lemmas always use medial.
    """
    nfd = unicodedata.normalize('NFD', text)
    stripped = ''.join(
        c for c in nfd
        if not (0x0591 <= ord(c) <= 0x05C7   # Hebrew accents, points, shin/sin dots
                or 0x0300 <= ord(c) <= 0x036F)  # general combining (diacritics)
    )
    return stripped.translate(_FINAL_TO_MEDIAL)


def _normalize_latin(text: str) -> str:
    """Match normalize_key() in build_latin_lexicon.py exactly."""
    text = re.sub(r'[\x80-\x9f]', '', text)
    text = text.lower()
    text = text.replace('æ', 'ae').replace('œ', 'oe')
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nfd if not unicodedata.combining(c))


_WIN_INVALID = re.compile(r'[\\/:*?"<>|]')

def _safe_dirname(key: str) -> str:
    """Filesystem-safe directory name for a lemma key.

    Keeps Unicode characters (Hebrew, Greek, Latin) intact so that
    Python's http.server and Cloudflare Pages can resolve URLs by
    URL-decoding the request path and matching the directory name.
    Only the characters that Windows NTFS disallows are percent-encoded.
    """
    safe = _WIN_INVALID.sub(lambda m: f'%{ord(m.group()):02X}', key)
    return safe or '_'


def parse_oshb_morph(code: str) -> dict:
    """Parse an OpenScriptures Hebrew/Aramaic morph code to UD-style morph_thb fields.

    Format: [H|A] + pos-letter + positional fields
      The leading H (Hebrew) or A (Aramaic) language prefix is optional —
      about 35% of tokens in the data omit it (e.g. standalone nouns after
      a split article). Strip it when present, proceed either way.
      N/A: type(skip) gender number state
      V:   stem aspect [person] gender number [state]
      P/S: type(skip) person gender number
      T:   subtype  (d=article, j=interjection, else particle)
      R/C/D: no extra fields
    """
    if not code:
        return {}
    # Strip optional language prefix
    if code[0] in ('H', 'A'):
        code = code[1:]
    if not code:
        return {}
    pos_code = code[0]

    GENDER = {'m': 'masculine', 'f': 'feminine', 'b': 'common', 'c': 'common'}
    NUMBER = {'s': 'singular',  'p': 'plural',   'd': 'dual'}
    STATE  = {'a': 'absolute',  'c': 'construct', 'd': 'determined'}

    if pos_code == 'T':
        sub = code[1] if len(code) > 1 else ''
        return {'pos': {'d': 'article', 'j': 'interjection'}.get(sub, 'particle')}

    POS = {'N': 'noun', 'V': 'verb', 'A': 'adjective', 'D': 'adverb',
           'R': 'preposition', 'C': 'conjunction', 'P': 'pronoun', 'S': 'pronoun'}
    result: dict = {'pos': POS.get(pos_code, '')}

    if pos_code in ('N', 'A'):
        # [1]=type (skip), [2]=gender, [3]=number, [4]=state
        g, n, s = code[2:3], code[3:4], code[4:5]
        if g: result['gender'] = GENDER.get(g, '')
        if n: result['number'] = NUMBER.get(n, '')
        if s: result['state']  = STATE.get(s, '')

    elif pos_code == 'V':
        STEM_NAME = {
            'q': 'Qal',        'N': 'Niphal',       'p': 'Piel',      'P': 'Pual',
            'h': 'Hiphil',     'H': 'Hophal',        't': 'Hithpael',  'o': 'Polel',
            'O': 'Polal',      'r': 'Poel',          'R': 'Poal',      'u': 'Hithpolel',
            'k': 'Qal passive','K': 'Pilpel',        'z': 'Palpel',    'c': 'Nithpael',
            'D': 'Hithpalpel', 'w': 'Hithpoel',      'm': 'Palel',     'M': 'Pulal',
            'g': 'Pilel',      'G': 'Pulel',         'f': 'Pilpel',
            'b': 'Shaphel',    'B': "Haph'el",
        }
        STEM_VOICE = {
            'q': 'active',   'N': 'passive',   'p': 'active',    'P': 'passive',
            'h': 'active',   'H': 'passive',   't': 'reflexive', 'o': 'active',
            'O': 'passive',  'r': 'active',    'R': 'passive',   'u': 'reflexive',
            'k': 'passive',  'K': 'active',    'z': 'active',    'c': 'passive',
            'D': 'reflexive','w': 'reflexive', 'g': 'active',    'G': 'passive',
            'f': 'active',   'b': 'active',    'B': 'active',
        }
        ASPECT_TENSE = {'p': 'perfect', 'i': 'imperfect', 'w': 'imperfect (sequential)'}
        ASPECT_MOOD  = {
            'p': 'indicative',            'i': 'indicative',
            'w': 'indicative',            'h': 'cohortative',
            'j': 'jussive',               'v': 'imperative',
            'r': 'participle (active)',   's': 'participle (passive)',
            'a': 'infinitive (absolute)', 'c': 'infinitive (construct)',
        }
        stem   = code[1] if len(code) > 1 else ''
        aspect = code[2] if len(code) > 2 else ''
        rest   = code[3:]

        if stem   in STEM_NAME:    result['stem']  = STEM_NAME[stem]
        if stem   in STEM_VOICE:   result['voice'] = STEM_VOICE[stem]
        if aspect in ASPECT_TENSE: result['tense'] = ASPECT_TENSE[aspect]
        if aspect in ASPECT_MOOD:  result['mood']  = ASPECT_MOOD[aspect]

        # Consume remaining: [person] gender number [state]
        i = 0
        if i < len(rest) and rest[i] in ('1', '2', '3'):
            result['person'] = rest[i]; i += 1
        if i < len(rest) and rest[i] in GENDER:
            result['gender'] = GENDER[rest[i]]; i += 1
        if i < len(rest) and rest[i] in NUMBER:
            result['number'] = NUMBER[rest[i]]; i += 1
        if i < len(rest) and rest[i] in STATE:
            result['state']  = STATE[rest[i]]; i += 1

    elif pos_code in ('P', 'S'):
        # [1]=type (skip), [2]=person, [3]=gender, [4]=number
        p, g, n = code[2:3], code[3:4], code[4:5]
        if p in ('1', '2', '3'): result['person'] = p
        if g: result['gender'] = GENDER.get(g, '')
        if n: result['number'] = NUMBER.get(n, '')

    return {k: v for k, v in result.items() if v}


def parse_lxx_morph(code: str) -> dict:
    """Parse a CATSS-style LXX morph code to UD-style morph_thb fields.

    Format: {POS}{subclass?}-{inflection}
      Nouns/adj/pronouns: case+number+gender  e.g. N2-NSM  RD-DSM
      Verbs: tense+voice+mood+person?+number? e.g. V9-IAI3S  V-AAN
      Particles: no inflection field          e.g. P  C  D
    """
    if not code:
        return {}

    parts = code.split('-', 1)
    pos_raw  = parts[0]
    form     = parts[1] if len(parts) > 1 else ''

    # Part of speech
    POS = {
        'N': 'noun',        'A': 'adjective',   'D': 'adverb',
        'P': 'preposition', 'C': 'conjunction',  'I': 'interjection',
        'V': 'verb',
        'RA': 'article',    'RD': 'pronoun',     'RI': 'pronoun',
        'RR': 'pronoun',    'RP': 'pronoun',     'RX': 'pronoun',
        'T': 'article',
    }
    # Match longest prefix first for two-letter codes
    pos = ''
    if pos_raw[:2] in POS:
        pos = POS[pos_raw[:2]]
    elif pos_raw[:1] in POS:
        pos = POS[pos_raw[:1]]
    if not pos:
        return {}

    result: dict = {'pos': pos}

    CASE   = {'N': 'nominative', 'G': 'genitive', 'D': 'dative',
               'A': 'accusative', 'V': 'vocative'}
    NUMBER = {'S': 'singular', 'P': 'plural', 'D': 'dual'}
    GENDER = {'M': 'masculine', 'F': 'feminine', 'N': 'neuter'}

    if pos in ('noun', 'adjective', 'pronoun', 'article'):
        # form = case + number + gender (gender optional for indeclinables)
        if len(form) >= 1: result['case']   = CASE.get(form[0], '')
        if len(form) >= 2: result['number'] = NUMBER.get(form[1], '')
        if len(form) >= 3: result['gender'] = GENDER.get(form[2], '')

    elif pos == 'verb':
        TENSE = {'P': 'present',   'I': 'imperfect', 'F': 'future',
                 'A': 'aorist',    'X': 'perfect',   'Y': 'pluperfect',
                 'R': 'perfect',   'L': 'pluperfect'}
        VOICE = {'A': 'active',    'M': 'middle',    'P': 'passive',
                 'E': 'middle or passive', 'D': 'middle'}
        MOOD  = {'I': 'indicative','S': 'subjunctive','O': 'optative',
                 'M': 'imperative','N': 'infinitive', 'P': 'participle'}
        if len(form) >= 1: result['tense'] = TENSE.get(form[0], '')
        if len(form) >= 2: result['voice'] = VOICE.get(form[1], '')
        if len(form) >= 3: result['mood']  = MOOD.get(form[2], '')
        if len(form) >= 4 and form[3] in ('1', '2', '3'):
            result['person'] = form[3]
            if len(form) >= 5: result['number'] = NUMBER.get(form[4], '')
        elif len(form) >= 4:  # participle inflection: case+number+gender
            result['case']   = CASE.get(form[3], '')
            if len(form) >= 5: result['number'] = NUMBER.get(form[4], '')
            if len(form) >= 6: result['gender'] = GENDER.get(form[5], '')

    return {k: v for k, v in result.items() if v}


def parse_sp_morph(code: str) -> dict:
    """Parse a Samaritan Pentateuch (ETCBC-encoded) morph code to UD-style fields.

    Format: POS|[VERBFORM|][field=value|...]
    Examples:
      subs|gn=m|nu=sg           → noun, masculine singular
      verb|impf|ps=p3|gn=m|nu=sg|stem=]]   → 3ms imperfect Qal verb
      art                       → article
      prep                      → preposition

    Stem codes use ETCBC bracket-notation where ]X] encodes the stem's
    characteristic consonants:
      ]]  = Qal          ]H]   = Hiphil       ]N]   = Niphal
      ]T] = Hithpael     ]HT]  = Hithpael     ]W]   = Hophal (impf forms)
      ]HW]= Hophal       ]CT]  = Hishtaphel   ]HCT] = Hishtaphel
      ]F] = Hithpolel    ]HF]  = Hithpolel    ]S]   = Hithpolel
      ]Y] = Hithpael     ]>]   = Qal (hollow root variant)
    """
    if not code:
        return {}
    parts = code.split('|')
    pos_raw = parts[0]

    POS_MAP = {
        'subs': 'noun',         'nmpr': 'proper noun',
        'adjv': 'adjective',    'advb': 'adverb',
        'verb': 'verb',
        'art':  'article',      'prep': 'preposition',
        'conj': 'conjunction',  'intj': 'interjection',
        'prps': 'pronoun',      'prde': 'pronoun',
        'prre': 'pronoun',      'prin': 'pronoun',
        'inrg': 'particle',     'nega': 'particle',
    }
    pos = POS_MAP.get(pos_raw)
    if not pos:
        return {}
    result: dict = {'pos': pos}

    # Verb aspect/tense codes (second pipe segment when POS is verb)
    ASPECT_MAP = {
        'perf': ('perfect',               'indicative'),
        'impf': ('imperfect',             'indicative'),
        'wayq': ('imperfect (sequential)', 'indicative'),
        'impv': (None,                    'imperative'),
        'infa': (None,                    'infinitive (absolute)'),
        'infc': (None,                    'infinitive (construct)'),
        'ptca': (None,                    'participle (active)'),
        'ptcp': (None,                    'participle (passive)'),
    }
    STEM_MAP = {
        ']]':    'Qal',          ']H]':   'Hiphil',
        ']N]':   'Niphal',       ']T]':   'Hithpael',
        ']HT]':  'Hithpael',     ']W]':   'Hophal',
        ']HW]':  'Hophal',       ']CT]':  'Hishtaphel',
        ']HCT]': 'Hishtaphel',   ']>]':   'Qal',
        ']F]':   'Hithpolel',    ']HF]':  'Hithpolel',
        ']S]':   'Hithpolel',    ']Y]':   'Hithpael',
    }
    STEM_VOICE = {
        'Qal': 'active',        'Hiphil': 'active',
        'Niphal': 'passive',    'Hithpael': 'reflexive',
        'Hophal': 'passive',    'Hishtaphel': 'reflexive',
        'Hithpolel': 'reflexive',
    }
    PERSON_MAP = {'p1': '1', 'p2': '2', 'p3': '3'}
    GENDER_MAP = {'m': 'masculine', 'f': 'feminine', 'c': 'common', 'b': 'common'}
    NUMBER_MAP = {'sg': 'singular', 'pl': 'plural',  'du': 'dual'}
    STATE_MAP  = {'a': 'absolute',  'c': 'construct', 'd': 'determined'}

    field_idx = 1
    if pos_raw == 'verb' and len(parts) > 1 and parts[1] in ASPECT_MAP:
        tense, mood = ASPECT_MAP[parts[1]]
        if tense: result['tense'] = tense
        if mood:  result['mood']  = mood
        field_idx = 2

    for part in parts[field_idx:]:
        if '=' not in part:
            continue
        key, val = part.split('=', 1)
        if key == 'ps':
            p = PERSON_MAP.get(val)
            if p: result['person'] = p
        elif key == 'gn':
            g = GENDER_MAP.get(val)
            if g: result['gender'] = g
        elif key == 'nu':
            n = NUMBER_MAP.get(val)
            if n: result['number'] = n
        elif key == 'st':
            s = STATE_MAP.get(val)
            if s: result['state'] = s
        elif key == 'stem':
            stem = STEM_MAP.get(val)
            if stem:
                result['stem']  = stem
                voice = STEM_VOICE.get(stem)
                if voice: result['voice'] = voice

    return {k: v for k, v in result.items() if v}


class VersificationMapper:
    """Maps MT (org) verse references to their equivalents in LXX and VUL.

    Copenhagen Alliance mappedVerses keys are tradition-space; values are org/MT-space.
    We invert the map so we can go org → tradition at build time.
    """

    CANON_TO_CODE: Dict[str, str] = {
        'Genesis': 'GEN', 'Exodus': 'EXO', 'Leviticus': 'LEV',
        'Numbers': 'NUM', 'Deuteronomy': 'DEU', 'Joshua': 'JOS',
        'Judges': 'JDG', 'Ruth': 'RUT', '1Samuel': '1SA', '2Samuel': '2SA',
        '1Kings': '1KI', '2Kings': '2KI', '1Chronicles': '1CH', '2Chronicles': '2CH',
        'Ezra': 'EZR', 'Nehemiah': 'NEH', 'Esther': 'EST', 'Job': 'JOB',
        'Psalms': 'PSA', 'Proverbs': 'PRO', 'Ecclesiastes': 'ECC', 'Song': 'SNG',
        'Isaiah': 'ISA', 'Jeremiah': 'JER', 'Lamentations': 'LAM',
        'Ezekiel': 'EZK', 'Daniel': 'DAN', 'Hosea': 'HOS', 'Joel': 'JOL',
        'Amos': 'AMO', 'Obadiah': 'OBA', 'Jonah': 'JON', 'Micah': 'MIC',
        'Nahum': 'NAM', 'Habakkuk': 'HAB', 'Zephaniah': 'ZEP', 'Haggai': 'HAG',
        'Zechariah': 'ZEC', 'Malachi': 'MAL',
    }
    CODE_TO_CANON: Dict[str, str] = {v: k for k, v in CANON_TO_CODE.items()}

    # Tradition-specific display names for book codes where the LXX uses a different title
    LXX_DISPLAY: Dict[str, str] = {
        '1SA': 'I Kingdoms', '2SA': 'II Kingdoms',
        '1KI': 'III Kingdoms', '2KI': 'IV Kingdoms',
    }
    VUL_DISPLAY: Dict[str, str] = {}
    KJV_DISPLAY: Dict[str, str] = {}

    TRADITION_FILES = (
        ('lxx', 'lxx.json'),
        ('vul', 'vul.json'),
        ('kjv', 'eng.json'),
    )

    def __init__(self, mapping_source: Path):
        self._maps: Dict[str, Dict] = {}
        self._display: Dict[str, Dict[str, str]] = {
            'lxx': self.LXX_DISPLAY,
            'vul': self.VUL_DISPLAY,
            'kjv': self.KJV_DISPLAY,
        }
        # Unified file (thb.1.3.versification.json) or legacy directory
        if mapping_source.is_file():
            self._load_unified(mapping_source)
        else:
            self._load_directory(mapping_source)

    def _load_unified(self, path: Path):
        """Load versification from unified thb.1.3.versification.json."""
        with open(path, encoding='utf-8') as f:
            unified = json.load(f)
        sources = unified.get('sources', {})

        lxx_combined = dict(sources.get('lxx', {}).get('data', {}).get('mappedVerses', {}))
        sup_data = sources.get('supplement', {}).get('data', {})
        sup_entries = sup_data.get('mappedVerses', sup_data)
        lxx_combined.update(sup_entries)
        print(f"  lxx supplement: {len(sup_entries)} additional entries merged")
        self._maps['lxx'] = self._build_inverted(lxx_combined)
        print(f"  lxx: {len(self._maps['lxx'])} org->tradition verse mappings loaded")

        for tradition, key in [('vul', 'vul'), ('kjv', 'kjv')]:
            combined = dict(sources.get(key, {}).get('data', {}).get('mappedVerses', {}))
            self._maps[tradition] = self._build_inverted(combined)
            print(f"  {tradition}: {len(self._maps[tradition])} org->tradition verse mappings loaded")

    def _load_directory(self, mapping_dir: Path):
        """Legacy: load versification from directory of individual JSON files."""
        for tradition, filename in self.TRADITION_FILES:
            p = mapping_dir / filename
            if not p.exists():
                print(f"Warning: {p} not found — versification mapping for {tradition} disabled")
                self._maps[tradition] = {}
                continue
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
            combined = dict(data.get('mappedVerses', {}))

            if tradition == 'lxx':
                sup_path = mapping_dir / 'lxx_supplement.json'
                if sup_path.exists():
                    with open(sup_path, encoding='utf-8') as f:
                        sup = json.load(f)
                    sup_entries = sup.get('mappedVerses', {})
                    combined.update(sup_entries)
                    print(f"  lxx supplement: {len(sup_entries)} additional entries merged")

            self._maps[tradition] = self._build_inverted(combined)
            print(f"  {tradition}: {len(self._maps[tradition])} org->tradition verse mappings loaded")

    @staticmethod
    def _expand(ref: str) -> List[Tuple[str, int, int]]:
        """Parse 'BOOK CH:V' or 'BOOK CH:V-V2' → list of (book_code, ch, v) tuples."""
        m = re.match(r'^(\S+)\s+(\d+):(\d+)(?:-(\d+))?$', ref.strip())
        if not m:
            return []
        book, ch = m.group(1), int(m.group(2))
        v1 = int(m.group(3))
        v2 = int(m.group(4)) if m.group(4) else v1
        return [(book, ch, v) for v in range(v1, v2 + 1)]

    def _build_inverted(self, mapped_verses: dict) -> dict:
        """Invert mappedVerses: (trad→org) becomes (org→[trad])."""
        inverted: Dict[tuple, List[tuple]] = {}
        for trad_ref, org_ref in mapped_verses.items():
            trad = self._expand(trad_ref)
            org  = self._expand(org_ref)
            if not trad or not org or len(trad) != len(org):
                continue
            for org_entry, trad_entry in zip(org, trad):
                if org_entry == trad_entry:
                    continue  # skip identity mappings
                inverted.setdefault(org_entry, []).append(trad_entry)
        return inverted

    def resolve(self, tradition: str, canonical_book: str, chapter: int, verse: int
                ) -> Optional[Tuple[List[Tuple[int, int]], str]]:
        """Resolve an MT verse to its equivalent in the given tradition.

        Returns None when the mapping is 1:1 (same book/chapter/verse — no label needed).
        Otherwise returns ([(trad_ch, trad_v), ...], display_label).
        The caller fetches the content for each (trad_ch, trad_v) and concatenates words.
        """
        org_code = self.CANON_TO_CODE.get(canonical_book)
        if not org_code:
            return None
        entries = self._maps.get(tradition, {}).get((org_code, chapter, verse))
        if not entries:
            return None

        trad_refs = [(e[1], e[2]) for e in entries]
        trad_book_code = entries[0][0]

        display_map = self._display.get(tradition, {})
        book_display = display_map.get(trad_book_code) or self.CODE_TO_CANON.get(trad_book_code, trad_book_code)

        if len(trad_refs) == 1:
            trad_ch, trad_v = trad_refs[0]
            verse_part = f"{trad_ch}:{trad_v}"
        else:
            first_ch, first_v = trad_refs[0]
            last_ch,  last_v  = trad_refs[-1]
            verse_part = (f"{first_ch}:{first_v}–{last_v}"
                          if first_ch == last_ch
                          else f"{first_ch}:{first_v}–{last_ch}:{last_v}")

        label = (f"{book_display} {verse_part}"
                 if trad_book_code != org_code
                 else verse_part)

        return trad_refs, label


def parse_dss_morph(code: str) -> dict:
    """Parse a Text-Fabric DSS morphology code → UD-style dict."""
    if not code or code in ('unknown', 'verb:unknown', 'verb'):
        return {}
    TENSE_MAP = {
        'perf': 'perfect', 'impf': 'imperfect', 'wayy': 'wayyiqtol',
        'impv': 'imperative', 'infc': 'infinitive construct',
        'infa': 'infinitive absolute', 'ptca': 'active participle',
        'ptcp': 'passive participle',
    }
    base, _, vf = code.partition(':')
    if base == 'verb':
        result: dict = {'pos': 'verb'}
        if vf in ('perf', 'impf', 'wayy'):
            result['tense'] = TENSE_MAP[vf]
        elif vf == 'impv':
            result['mood'] = TENSE_MAP[vf]
        elif vf in ('infc', 'infa'):
            result['mood'] = TENSE_MAP[vf]
        elif vf in ('ptca', 'ptcp'):
            result['aspect'] = TENSE_MAP[vf]
        return result
    POS_MAP = {
        'subs': 'noun', 'ptcl': 'particle', 'adjv': 'adjective',
        'pron': 'pronoun', 'numr': 'numeral', 'suff': 'suffix',
    }
    pos = POS_MAP.get(base, '')
    return {'pos': pos} if pos else {}


class THBSiteBuilder:
    def __init__(self, backend_dir: str, template_path: str, output_dir: str):
        """Initialize the site builder with required file paths."""
        self.backend_dir = backend_dir
        self.template_path = template_path
        self.output_dir = output_dir
        
        # Chapter counts for navigation - Fixed Malachi to 3 chapters
        self.chapter_counts = {
            'Genesis': 50, 'Exodus': 40, 'Leviticus': 27, 'Numbers': 36, 'Deuteronomy': 34,
            'Joshua': 24, 'Judges': 21, 'Ruth': 4, '1Samuel': 31, '2Samuel': 24,
            '1Kings': 22, '2Kings': 25, '1Chronicles': 29, '2Chronicles': 36,
            'Ezra': 10, 'Nehemiah': 13, 'Esther': 10, 'Job': 42, 'Psalms': 150,
            'Proverbs': 31, 'Ecclesiastes': 12, 'Song': 8, 'Isaiah': 66, 'Jeremiah': 52,
            'Lamentations': 5, 'Ezekiel': 48, 'Daniel': 12, 'Hosea': 14, 'Joel': 3,
            'Amos': 9, 'Obadiah': 1, 'Jonah': 4, 'Micah': 7, 'Nahum': 3, 'Habakkuk': 3,
            'Zephaniah': 3, 'Haggai': 2, 'Zechariah': 14, 'Malachi': 3
        }
        
        self.tradition_mapping = {
            'mt': 'hebrew',
            'lxx': 'greek', 
            'vul': 'latin',
            'sp': 'samaritan',
            'kjv': 'english',
            'dss': 'qumran'
        }
        
        # Load data
        self.thb_data = None
        self.template = None
        self.concordance_data: Dict[str, Any] = {}   # book → ch → verse → {H…: {Book: count}}
        self.mt_lexicon: Dict[str, Any] = {}
        self.lxx_lexicon: Dict[str, Any] = {}
        self.vul_lexicon: Dict[str, Any] = {}
        self.mapper: Optional[VersificationMapper] = None
        # Reverse index: consonantal Hebrew → MT Strong's code, built from mt_lexicon
        self._sp_consonant_index: Dict[str, str] = {}
        # Occurrence index: tradition → lemma_key → [(book, ch, verse), ...]
        self.occurrence_index: Dict[str, Dict[str, List[Tuple[str, int, int]]]] = {}
        # Hapax legomena: tradition → set of lemma_keys with exactly 1 verse occurrence
        self.hapax: Dict[str, Set[str]] = {}

    def get_tradition_book_name(self, canonical_book: str, tradition: str) -> str:
        """Get the correct book name for a specific tradition."""
        
        # Book name variations by tradition
        book_name_map = {
            # Books with potential numbering issues - JSON uses spaces
            '1Samuel': {
                'mt': '1 Samuel',
                'lxx': '1 Samuel', 
                'vul': '1 Samuel',
                'sp': 'NOT_AVAILABLE',
                'kjv': '1 Samuel',
                'dss': '1 Samuel'
            },
            '2Samuel': {
                'mt': '2 Samuel',
                'lxx': '2 Samuel',
                'vul': '2 Samuel', 
                'sp': 'NOT_AVAILABLE',
                'kjv': '2 Samuel',
                'dss': '2 Samuel'
            },
            '1Kings': {
                'mt': '1 Kings',
                'lxx': '1 Kings',
                'vul': '1 Kings',
                'sp': 'NOT_AVAILABLE', 
                'kjv': '1 Kings',
                'dss': '1 Kings'
            },
            '2Kings': {
                'mt': '2 Kings',
                'lxx': '2 Kings',
                'vul': '2 Kings',
                'sp': 'NOT_AVAILABLE',
                'kjv': '2 Kings', 
                'dss': '2 Kings'
            },
            '1Chronicles': {
                'mt': '1 Chronicles',
                'lxx': '1 Chronicles',
                'vul': '1 Chronicles',
                'sp': 'NOT_AVAILABLE',
                'kjv': '1 Chronicles',
                'dss': '1 Chronicles'
            },
            '2Chronicles': {
                'mt': '2 Chronicles', 
                'lxx': '2 Chronicles',
                'vul': '2 Chronicles',
                'sp': 'NOT_AVAILABLE',
                'kjv': '2 Chronicles',
                'dss': '2 Chronicles'
            },
            'Song': {
                'mt': 'Song of Solomon',
                'lxx': 'Song of Solomon',
                'vul': 'Song of Solomon',
                'sp': 'NOT_AVAILABLE',
                'kjv': 'Song of Solomon',
                'dss': 'Song of Solomon'
            }
        }
        
        # Add non-Torah books (no SP)
        non_torah_books = ['Joshua', 'Judges', 'Ruth', 'Esther', 'Job', 'Psalms', 
                          'Proverbs', 'Ecclesiastes', 'Isaiah', 'Jeremiah', 
                          'Lamentations', 'Ezekiel', 'Daniel', 'Hosea', 'Joel',
                          'Amos', 'Obadiah', 'Jonah', 'Micah', 'Nahum', 'Habakkuk',
                          'Zephaniah', 'Haggai', 'Zechariah', 'Malachi']
        
        for book in non_torah_books:
            # Special cases for LXX book names
            if book == 'Joshua':
                book_name_map[book] = {
                    'mt': book, 'lxx': 'Joshua B', 'vul': book,
                    'sp': 'NOT_AVAILABLE', 'kjv': book, 'dss': book
                }
            elif book == 'Judges':
                book_name_map[book] = {
                    'mt': book, 'lxx': 'Judges B', 'vul': book,
                    'sp': 'NOT_AVAILABLE', 'kjv': book, 'dss': book
                }
            else:
                book_name_map[book] = {
                    'mt': book, 'lxx': book, 'vul': book, 
                    'sp': 'NOT_AVAILABLE', 'kjv': book, 'dss': book
                }
        
        # Add Torah books (have all traditions)
        torah_books = ['Genesis', 'Exodus', 'Leviticus', 'Numbers', 'Deuteronomy']
        for book in torah_books:
            book_name_map[book] = {
                'mt': book, 'lxx': book, 'vul': book, 
                'sp': book, 'kjv': book, 'dss': book
            }
        
        if canonical_book in book_name_map:
            return book_name_map[canonical_book].get(tradition, canonical_book)
        
        # Default fallback
        if tradition == 'sp' and canonical_book not in torah_books:
            return 'NOT_AVAILABLE'
        
        return canonical_book
        
    def load_data(self):
        """Load all required data files."""
        backend = Path(self.backend_dir)

        print("Loading THB tradition data...")
        self.thb_data = {}
        for trad in ('mt', 'lxx', 'vul', 'sp', 'kjv', 'dss'):
            path = backend / f'thb.1.3.{trad}.json'
            if path.exists():
                print(f"  Loading thb.1.3.{trad}.json ({path.stat().st_size // 1024 // 1024} MB)...")
                with open(path, 'r', encoding='utf-8') as f:
                    self.thb_data[trad] = json.load(f)

        print("Loading template...")
        with open(self.template_path, 'r', encoding='utf-8') as f:
            self.template = f.read()

        print("Loading 1.4 split lexicon files...")
        for _trad, _attr in [('mt', 'mt_lexicon'), ('lxx', 'lxx_lexicon'), ('vul', 'vul_lexicon')]:
            _path = backend / f'thb.1.4.lexicon.{_trad}.json'
            with open(_path, encoding='utf-8') as f:
                _data = json.load(f)
            setattr(self, _attr, _data.get('entries', {}))
            print(f"  {_trad}: {len(getattr(self, _attr)):,} entries")

        print("Loading concordance (per-book)...")
        concordance_dir = backend / 'concordance'
        self.concordance_data = {}
        total = 0
        for filepath in sorted(concordance_dir.glob('*.json')):
            with open(filepath, encoding='utf-8') as f:
                book_con = json.load(f)
            book_name = book_con['book']
            self.concordance_data[book_name] = book_con['entries']
            total += sum(len(vv) for vv in book_con['entries'].values())
        print(f"  {total:,} verses loaded")

        # Build SP consonantal reverse-index
        for _code, _entry in self.mt_lexicon.items():
            if not re.fullmatch(r'\d+', _code):
                continue
            _heb = _entry.get('hebrew', '')
            if not (_heb and re.search(r'[֐-׿]', _heb)):
                continue
            for _token in _heb.split():
                _token = _token.strip('.,;:')
                if not re.search(r'[֐-׿]', _token):
                    continue
                _cons = _strip_nikkud(_token)
                if _cons and _cons not in self._sp_consonant_index:
                    self._sp_consonant_index[_cons] = _code
        print(f"  SP consonantal index: {len(self._sp_consonant_index)} entries")

        print("Loading versification mappings...")
        self.mapper = VersificationMapper(backend / 'thb.1.3.versification.json')

        print("Loading Aleppo Codex page index...")
        aleppo_path = backend / 'thb.1.3.aleppo.json'
        if aleppo_path.exists():
            with open(aleppo_path, encoding='utf-8') as f:
                self.aleppo_pages = json.load(f)
            print(f"  {len(self.aleppo_pages)} facsimile pages loaded")
        else:
            self.aleppo_pages = []
            print("  thb.1.3.aleppo.json not found — Aleppo links disabled")

        self.build_concordance_data()

    def _aleppo_url(self, book: str, chapter: int, verse: int) -> Optional[str]:
        """Return the archive.org facsimile URL for the Aleppo page containing
        (book, chapter, verse), or None if the verse is not attested."""
        for entry in self.aleppo_pages:
            same_book = entry['book'] == entry['closing_book']
            if same_book:
                if (book == entry['book']
                        and (chapter, verse) >= (entry['ch_from'], entry['v_from'])
                        and (chapter, verse) <= (entry['ch_to'], entry['v_to'])):
                    return self._aleppo_archive_url(entry['file'])
            else:
                if (book == entry['book']
                        and (chapter, verse) >= (entry['ch_from'], entry['v_from'])):
                    return self._aleppo_archive_url(entry['file'])
                if (book == entry['closing_book']
                        and (chapter, verse) <= (entry['ch_to'], entry['v_to'])):
                    return self._aleppo_archive_url(entry['file'])
        return None

    @staticmethod
    def _aleppo_archive_url(file_num: int) -> str:
        n = file_num if file_num % 2 == 1 else file_num - 1
        return f'https://archive.org/details/Aleppo_Codex_fascimile_edition/page/n{n}/mode/2up'

    def _lemma_key(self, word: Dict[str, Any], tradition: str) -> str:
        """Canonical lookup key for a word token in a given tradition."""
        if tradition == 'mt':
            parts = word.get('lemma', '').strip().split()
            raw = parts[0] if parts else ''
            return f'H{raw}' if raw and re.fullmatch(r'\d+', raw) else ''
        if tradition == 'lxx':
            return word.get('lemma', '').strip() or ''
        if tradition == 'vul':
            return _normalize_latin(word.get('lemma', '')) or ''
        if tradition == 'kjv':
            return word.get('strongs', '') or ''
        if tradition == 'sp':
            raw = word.get('lemma', '')
            clean = re.sub(r'[=\[/]+$', '', raw).strip()
            return _strip_nikkud(clean) or ''
        if tradition == 'dss':
            raw = word.get('lemma', '')
            clean = re.sub(r'_\d+$', '', raw).strip()
            return _strip_nikkud(clean) or ''
        return ''

    def build_concordance_data(self):
        """Build per-tradition occurrence lists from loaded text data.

        Populates self.occurrence_index and self.hapax.
        Deduplicates by (book, chapter, verse) — a lemma appearing twice in
        the same verse is counted once for concordance purposes.
        """
        print("Building concordance occurrence index...")
        for tradition in ('mt', 'lxx', 'vul', 'sp', 'kjv', 'dss'):
            index: Dict[str, List[Tuple[str, int, int]]] = {}
            seen: Dict[str, Set[Tuple[str, int, int]]] = {}
            trad_data = self.thb_data.get(tradition, {})
            for canon_book in self.chapter_counts:
                trad_book_name = self.get_tradition_book_name(canon_book, tradition)
                if trad_book_name == 'NOT_AVAILABLE':
                    continue
                for chap in trad_data.get(trad_book_name, []):
                    ch = chap.get('chapter')
                    for verse in chap.get('verses', []):
                        vn = verse.get('verse')
                        ref = (canon_book, ch, vn)
                        if tradition == 'dss':
                            words = [w for s in verse.get('scrolls', [])
                                     for w in s.get('words', [])]
                        else:
                            words = verse.get('words', [])
                        for word in words:
                            if word.get('is_sof_pasuq'):
                                continue
                            key = self._lemma_key(word, tradition)
                            if not key:
                                continue
                            if ref not in seen.setdefault(key, set()):
                                index.setdefault(key, []).append(ref)
                                seen[key].add(ref)
            self.occurrence_index[tradition] = index
            hapax = {k for k, v in index.items() if len(v) == 1}
            self.hapax[tradition] = hapax
            total_occ = sum(len(v) for v in index.values())
            print(f"  {tradition}: {len(index):,} lemmas, "
                  f"{len(hapax):,} hapax, {total_occ:,} total occurrences")

    _TRADITION_LABELS: Dict[str, str] = {
        'mt':  'Hebrew — Masoretic Text',
        'lxx': 'Greek — Septuagint',
        'vul': 'Latin — Vulgate',
        'kjv': 'English — KJV',
        'dss': 'Hebrew — Dead Sea Scrolls',
        'sp':  'Hebrew — Samaritan Pentateuch',
    }
    _TRADITION_LANG: Dict[str, str] = {
        'mt': 'he', 'lxx': 'el', 'vul': 'la',
        'kjv': 'en', 'dss': 'he', 'sp': 'he',
    }

    def _concordance_definition(self, tradition: str, key: str) -> str:
        """Definition HTML for a concordance stub page."""
        if tradition in ('mt', 'kjv', 'sp', 'dss'):
            code = key[1:] if key.startswith('H') else key
            entry = self.mt_lexicon.get(code, {})
            parts = []
            if entry.get('thb_def'):
                parts.append(f'<p class="def-thb">{html.escape(entry["thb_def"])}</p>')
            if entry.get('strongs_def'):
                parts.append(f'<p class="def-strongs"><em>Strong\'s:</em> '
                              f'{html.escape(entry["strongs_def"])}</p>')
            if entry.get('bdb_def'):
                bdb = entry['bdb_def'].replace('\n\n', '<hr>').replace('\n', '<br>')
                parts.append(f'<div class="def-bdb"><em>BDB:</em> {bdb}</div>')
            return ''.join(parts)
        if tradition == 'lxx':
            entry = self.lxx_lexicon.get(key, {})
            parts = []
            if entry.get('thb_def'):
                parts.append(f'<p class="def-thb">{html.escape(entry["thb_def"])}</p>')
            if entry.get('lsj_def'):
                parts.append(f'<p class="def-lsj"><em>LSJ:</em> '
                              f'{html.escape(entry["lsj_def"])}</p>')
            return ''.join(parts)
        if tradition == 'vul':
            entry = self.vul_lexicon.get(key, {})
            parts = []
            if entry.get('thb_def'):
                parts.append(f'<p class="def-thb">{html.escape(entry["thb_def"])}</p>')
            if entry.get('ls_def'):
                parts.append(f'<p class="def-ls"><em>L&amp;S:</em> '
                              f'{html.escape(entry["ls_def"])}</p>')
            return ''.join(parts)
        return ''

    _RTL_TRADITIONS = frozenset({'mt', 'sp', 'dss'})

    def _build_verse_lookup(self, tradition: str,
                            canon_book: str) -> Dict[Tuple[int, int], str]:
        """Return a (chapter, verse) → surface_text map for one book/tradition."""
        lookup: Dict[Tuple[int, int], str] = {}
        trad_book = self.get_tradition_book_name(canon_book, tradition)
        if trad_book == 'NOT_AVAILABLE':
            return lookup
        surf_key = 'surface_full' if tradition == 'dss' else 'surface'
        for chap in self.thb_data.get(tradition, {}).get(trad_book, []):
            ch = chap.get('chapter')
            for verse in chap.get('verses', []):
                v = verse.get('verse')
                if tradition == 'dss':
                    words = [w for s in verse.get('scrolls', [])
                             for w in s.get('words', [])]
                else:
                    words = verse.get('words', [])
                surfaces = [
                    w.get(surf_key) or w.get('surface', '')
                    for w in words
                    if not w.get('is_sof_pasuq')
                    and (w.get(surf_key) or w.get('surface'))
                ]
                lookup[(ch, v)] = ' '.join(s for s in surfaces if s)
        return lookup

    def build_concordance_stub_page(self, tradition: str, key: str,
                                    occurrences: List[Tuple[str, int, int]]) -> str:
        """Generate the HTML for a single concordance stub page."""
        trad_label = self._TRADITION_LABELS.get(tradition, tradition.upper())
        trad_lang  = self._TRADITION_LANG.get(tradition, 'und')
        trad_short = trad_label.split('—')[0].strip()
        is_rtl     = tradition in self._RTL_TRADITIONS
        text_dir   = 'rtl' if is_rtl else 'ltr'
        n = len(occurrences)
        disp_key  = html.escape(key)
        canon_url = (f'https://thb-concordance-{tradition}.pages.dev/'
                     f'{urlquote(key, safe="")}/')

        # Group by book (already in Bible order from build_concordance_data)
        book_groups: Dict[str, List[Tuple[int, int]]] = {}
        for book, ch, v in occurrences:
            book_groups.setdefault(book, []).append((ch, v))

        n_books = len(book_groups)
        books_word = 'book' if n_books == 1 else 'books'

        occ_parts = []
        for book, refs in book_groups.items():
            book_disp = html.escape(self.get_book_display_name(book))
            book_slug = book.lower()
            count     = len(refs)
            verse_word = 'verse' if count == 1 else 'verses'
            verse_lookup = self._build_verse_lookup(tradition, book)
            rows = []
            for ch, v in refs:
                verse_text = html.escape(verse_lookup.get((ch, v), ''))
                rows.append(
                    f'<div class="vrow">'
                    f'<a class="vref" href="/{book_slug}/{ch}/#v{v}">{ch}:{v}</a>'
                    f'<span class="vtext" dir="{text_dir}">{verse_text}</span>'
                    f'</div>'
                )
            occ_parts.append(
                f'<details class="book-group">'
                f'<summary class="book-summary">'
                f'<span class="book-name">{book_disp}</span>'
                f'<span class="book-count">{count} {verse_word}</span>'
                f'<span class="book-toggle">&#9654;</span>'
                f'</summary>'
                f'<div class="verse-list">{"".join(rows)}</div>'
                f'</details>'
            )
        occ_html = '\n'.join(occ_parts)
        def_html = self._concordance_definition(tradition, key) or '<em>No definition available.</em>'

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{disp_key} — {html.escape(trad_label)} | THB Concordance</title>
<link rel="canonical" href="{html.escape(canon_url)}"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;color:#1a1a1a;background:#faf9f7;padding:1.5rem 1rem;max-width:860px;margin:0 auto;line-height:1.6}}
header{{display:flex;align-items:center;gap:.5rem;font-size:.85rem;color:#666;margin-bottom:2rem;flex-wrap:wrap}}
header a{{color:#5a7a5a;text-decoration:none}}
header a:hover{{text-decoration:underline}}
.sep{{color:#ccc}}
h1{{font-size:2rem;font-weight:700;margin-bottom:.25rem}}
.badge{{display:inline-block;background:#f0efe8;border:1px solid #ddd;border-radius:4px;padding:.1rem .5rem;font-size:.8rem;color:#555;margin-bottom:1.25rem}}
.definition{{margin:1rem 0 1.5rem;padding:1rem;background:#fff;border:1px solid #e8e4dc;border-radius:6px;font-size:.9rem;color:#333}}
.def-thb{{font-weight:600;margin-bottom:.4rem}}
.def-strongs,.def-lsj,.def-ls{{color:#555;margin-top:.3rem}}
.def-bdb{{margin-top:.5rem;font-size:.85rem;color:#555}}
.occ-header{{display:flex;align-items:baseline;gap:.75rem;margin-bottom:.75rem}}
.occ-header h2{{font-size:1.05rem;font-weight:600}}
.occ-count{{font-size:.85rem;color:#888;flex:1}}
.expand-btn{{background:none;border:1px solid #ddd;border-radius:3px;color:#666;font-size:.78rem;padding:.15rem .55rem;cursor:pointer;font-family:inherit;transition:border-color .15s,color .15s;flex-shrink:0}}
.expand-btn:hover{{border-color:#5a7a5a;color:#5a7a5a}}
.book-group{{border-bottom:1px solid #f0ede6}}
.book-group:last-child{{border-bottom:none}}
.book-summary{{display:flex;align-items:center;gap:.75rem;padding:.4rem 0;cursor:pointer;list-style:none;user-select:none}}
.book-summary::-webkit-details-marker{{display:none}}
.book-summary:hover .book-name{{color:#5a7a5a}}
.book-name{{font-weight:600;font-size:.9rem;color:#333;flex:1}}
.book-count{{font-size:.8rem;color:#aaa;white-space:nowrap}}
.book-toggle{{font-size:.65rem;color:#bbb;transition:transform .15s;flex-shrink:0}}
details[open] .book-toggle{{transform:rotate(90deg)}}
.verse-list{{padding:.15rem 0 .6rem}}
.vrow{{display:flex;gap:.65rem;padding:.22rem 0;border-bottom:1px solid #f8f5f0;align-items:baseline}}
.vrow:last-child{{border-bottom:none}}
.vref{{color:#5a7a5a;font-size:.78rem;font-weight:600;min-width:2.6rem;flex-shrink:0;text-decoration:none;white-space:nowrap}}
.vref:hover{{text-decoration:underline}}
.vtext{{font-size:.875rem;color:#333;line-height:1.55;word-break:break-word}}
.vtext[dir=rtl]{{font-size:.975rem;text-align:right}}
footer{{margin-top:3rem;padding-top:1rem;border-top:1px solid #e0ddd6;font-size:.8rem;color:#999}}
footer a{{color:#888;text-decoration:none}}
</style>
</head>
<body>
<header>
  <a href="/">THB</a>
  <span class="sep">/</span>
  <span>Concordance</span>
  <span class="sep">/</span>
  <span>{html.escape(trad_short)}</span>
  <span class="sep">/</span>
  <span lang="{trad_lang}">{disp_key}</span>
</header>
<main>
  <h1 lang="{trad_lang}">{disp_key}</h1>
  <div class="badge">{html.escape(trad_label)}</div>
  <div class="definition">{def_html}</div>
  <div class="occ-header">
    <h2>Occurrences</h2>
    <span class="occ-count">{n} {'verse' if n == 1 else 'verses'} &middot; {n_books} {books_word}</span>
    <button class="expand-btn" onclick="toggleAll(this)">Expand all</button>
  </div>
  <div class="occ-list">{occ_html}</div>
</main>
<footer>
  <a href="https://hebrewbible.dev">Translator's Hebrew Bible</a> —
  <a href="/about/">About</a> · <a href="/license/">License</a>
</footer>
<script>
function toggleAll(btn){{
  var all=document.querySelectorAll('details.book-group');
  var open=!all[0]?.open;
  all.forEach(function(d){{d.open=open;}});
  btn.textContent=open?'Collapse all':'Expand all';
}}
</script>
</body>
</html>'''

    def build_concordance_stubs(self):
        """Write one static concordance page per non-hapax lemma per tradition."""
        print("\nBuilding concordance stub pages...")
        out_base = Path(self.output_dir) / 'concordance'
        total = 0
        hapax_skipped = 0
        for tradition, index in self.occurrence_index.items():
            hapax = self.hapax.get(tradition, set())
            stub_count = len(index) - len(hapax)
            print(f"  {tradition}: writing {stub_count:,} stubs "
                  f"(skipping {len(hapax):,} hapax)...")
            trad_dir = out_base / tradition
            for key, occurrences in index.items():
                if key in hapax:
                    hapax_skipped += 1
                    continue
                stub_dir = trad_dir / _safe_dirname(key)
                stub_dir.mkdir(parents=True, exist_ok=True)
                page = self.build_concordance_stub_page(tradition, key, occurrences)
                (stub_dir / 'index.html').write_text(page, encoding='utf-8')
                total += 1
        print(f"  Concordance: {total:,} stubs written, {hapax_skipped:,} hapax skipped.")

    def get_book_display_name(self, book: str) -> str:
        """Get the display name for a book."""
        display_names = {
            '1Samuel': '1 Samuel', '2Samuel': '2 Samuel', '1Kings': '1 Kings', '2Kings': '2 Kings',
            '1Chronicles': '1 Chronicles', '2Chronicles': '2 Chronicles', 'Song': 'Song of Songs'
        }
        return display_names.get(book, book)
    
    def build_word_span(self, word: Dict[str, Any], tradition: str) -> str:
        """Build a clickable word span with data attributes."""
        if word.get('is_sof_pasuq', False):
            return ''
            
        # Get surface text
        if tradition == 'dss':
            surface = word.get('surface_full', word.get('surface', ''))
            # Strip spaces that are adjacent to lacuna brackets in the raw data.
            # Source encodes e.g. '[ ו' (open-bracket space word) and 'ו ]'
            # (word space close-bracket); those spaces must not appear in output.
            surface = re.sub(r'\[ ', '[', surface)
            surface = re.sub(r' \]', ']', surface)
            surface = surface.strip()
        else:
            surface = word.get('surface', '')
            
        if not surface or surface in ['׃', '.', ',', ';', ':', '!', '?', '—', '–']:
            return ''
            
        # Get word class
        word_class = f"{self.tradition_mapping.get(tradition, tradition)}-word"

        morph = word.get('morph', '').replace('|', '\n')
        morph_thb = word.get('morph_thb', {})

        # Concordance / hapax attributes — baked in once occurrence index is ready
        _conc_key = self._lemma_key(word, tradition) if self.occurrence_index else ''
        if _conc_key:
            _is_hapax = _conc_key in self.hapax.get(tradition, set())
            _conc_attrs = (f' data-conc-key="{html.escape(_conc_key)}"'
                           + (' data-hapax="1"' if _is_hapax else ''))
        else:
            _conc_attrs = ''

        def resolve_hebrew_lemma(entry: dict, code: str) -> str:
            """Return the best available Hebrew lemma string for a Strong's entry.

            Many lexicon entries have the numeric code stored in the 'hebrew' field
            instead of real Unicode text (a pipeline gap affecting ~half the lexicon).
            When that happens, fall back to the transliteration in 'strongs_id'
            (e.g. 'H3513 — kabad' → 'kabad') rather than showing a bare number.
            """
            heb = entry.get('hebrew', '')
            if heb and re.search(r'[֐-׿]', heb):
                return heb  # real Hebrew/Aramaic script — use it
            sid = entry.get('strongs_id', '')
            m = re.search(r'—\s*(.+)', sid)
            if m:
                return m.group(1).strip()   # e.g. 'kabad'
            return f'H{code}'              # last resort: at least show 'H3513'

        def morph_thb_attrs(mai: dict) -> str:
            """Emit one data attribute per morph_thb field (omit absent ones)."""
            parts = []
            for field in ('pos', 'stem', 'tense', 'mood', 'voice', 'aspect',
                          'person', 'gender', 'number', 'state', 'case', 'degree'):
                v = mai.get(field)
                if v:
                    parts.append(f' data-{field}="{html.escape(str(v))}"')
            return ''.join(parts)

        if tradition == 'mt':
            raw_lemma = word.get('lemma', '')
            code = raw_lemma.strip().split()[0] if raw_lemma.strip() else raw_lemma
            entry = self.mt_lexicon.get(code, {})
            translit = entry.get('strongs_id', f'H{code}')
            lex_key = f'H{code}'
            if lex_key not in self._page_lex:
                self._page_lex[lex_key] = {
                    'bdb':     entry.get('bdb_def',     ''),
                    'thb':     entry.get('thb_def',     ''),
                    'strongs': entry.get('strongs_def', ''),
                }
            primary_morph = morph.split('\n')[0] if morph else ''
            mai = parse_oshb_morph(primary_morph)
            return (
                f'<span class="{word_class}"'
                f' data-lemma="{html.escape(resolve_hebrew_lemma(entry, code))}"'
                f' data-strongs="{html.escape(translit)}"'
                f' data-scode="{html.escape(lex_key)}"'
                f' data-morph="{html.escape(morph)}"'
                f'{morph_thb_attrs(mai)}'
                f'{_conc_attrs}'
                f'>{surface}</span>'
            )

        if tradition == 'lxx':
            lemma = word.get('lemma', '')
            entry = self.lxx_lexicon.get(lemma, {})
            if lemma and lemma not in self._page_lex:
                self._page_lex[lemma] = {
                    'bdb': entry.get('lsj_def', ''),
                    'thb':  entry.get('thb_def',  ''),
                }
            # Prefer stored morph_thb (Swete words); fall back to runtime
            # parse of CATSS morph code for any legacy words still carrying one.
            mai = morph_thb if morph_thb else parse_lxx_morph(morph.split('\n')[0] if morph else '')
            return (
                f'<span class="{word_class}"'
                f' data-lemma="{html.escape(lemma)}"'
                f' data-lexkey="{html.escape(lemma)}"'
                f'{morph_thb_attrs(mai)}'
                f' data-morph="{html.escape(morph)}"'
                f'{_conc_attrs}'
                f'>{surface}</span>'
            )

        if tradition == 'vul':
            lemma = word.get('lemma', '')
            norm = _normalize_latin(lemma)
            entry = self.vul_lexicon.get(norm, {})
            if norm and norm not in self._page_lex:
                self._page_lex[norm] = {
                    'bdb': entry.get('ls_def', ''),
                    'thb':  entry.get('thb_def', ''),
                }
            return (
                f'<span class="{word_class}"'
                f' data-lemma="{html.escape(lemma)}"'
                f' data-lexkey="{html.escape(norm)}"'
                f'{morph_thb_attrs(morph_thb)}'
                f'{_conc_attrs}'
                f'>{surface}</span>'
            )

        if tradition == 'kjv':
            sid = word.get('strongs')
            if sid:
                code = sid[1:]  # strip 'H' -> e.g. '7225'
                entry = self.mt_lexicon.get(code, {})
                translit = entry.get('strongs_id', sid)
                lex_key = sid  # e.g. 'H7225'
                if lex_key not in self._page_lex:
                    self._page_lex[lex_key] = {
                        'bdb':     entry.get('bdb_def',     ''),
                        'thb':     entry.get('thb_def',     ''),
                        'strongs': entry.get('strongs_def', ''),
                    }
                heb_lemma = resolve_hebrew_lemma(entry, code)
                raw_morph = word.get('mt_morph', '')
                mai = parse_oshb_morph(raw_morph) if raw_morph else {}
                return (
                    f'<span class="{word_class}"'
                    f' data-lemma="{html.escape(heb_lemma)}"'
                    f' data-strongs="{html.escape(translit)}"'
                    f' data-scode="{html.escape(lex_key)}"'
                    f' data-morph="{html.escape(raw_morph)}"'
                    f'{morph_thb_attrs(mai)}'
                    f'{_conc_attrs}'
                    f'>{surface}</span>'
                )
            # Article/conjunction/preposition — no Strong's mapping
            return f'<span class="{word_class}" data-lemma="{html.escape(surface)}"{_conc_attrs}>{surface}</span>'

        if tradition == 'sp':
            lemma_raw = word.get('lemma', '')
            # Strip ETCBC lemma markers: trailing [, =[, =, / characters
            lemma_clean = re.sub(r'[=\[/]+$', '', lemma_raw).strip()
            # Parse using raw morph (pipes intact) — `morph` already has | → \n substitution
            raw_morph = word.get('morph', '')
            mai = parse_sp_morph(raw_morph) if raw_morph else {}

            # Look up lemma in MT lexicon via consonantal reverse index.
            # Strip any residual shin/sin dots from the SP lemma so it matches
            # the fully-stripped MT consonantal form in the index.
            sp_code = self._sp_consonant_index.get(_strip_nikkud(lemma_clean), '')
            if sp_code:
                sp_entry   = self.mt_lexicon.get(sp_code, {})
                lex_key    = f'H{sp_code}'
                translit   = sp_entry.get('strongs_id', lex_key)
                disp_lemma = resolve_hebrew_lemma(sp_entry, sp_code)
                if lex_key not in self._page_lex:
                    self._page_lex[lex_key] = {
                        'bdb':     sp_entry.get('bdb_def',     ''),
                        'thb':     sp_entry.get('thb_def',     ''),
                        'strongs': sp_entry.get('strongs_def', ''),
                    }
                return (
                    f'<span class="{word_class}"'
                    f' data-lemma="{html.escape(disp_lemma)}"'
                    f' data-strongs="{html.escape(translit)}"'
                    f' data-scode="{html.escape(lex_key)}"'
                    f'{morph_thb_attrs(mai)}'
                    f' data-morph="{html.escape(morph)}"'
                    f'{_conc_attrs}'
                    f'>{surface}</span>'
                )
            # No MT match — render with consonantal lemma and morphology only
            return (
                f'<span class="{word_class}"'
                f' data-lemma="{html.escape(lemma_clean)}"'
                f'{morph_thb_attrs(mai)}'
                f' data-morph="{html.escape(morph)}"'
                f'{_conc_attrs}'
                f'>{surface}</span>'
            )

        # DSS — look up via MT consonantal index (mirrors SP path)
        if tradition == 'dss':
            lemma_raw   = word.get('lemma', '')
            lemma_clean = re.sub(r'_\d+$', '', lemma_raw).strip()
            raw_morph   = word.get('morph', '')
            mai         = parse_dss_morph(raw_morph) if raw_morph else {}
            dss_code    = self._sp_consonant_index.get(_strip_nikkud(lemma_clean), '')
            if dss_code:
                dss_entry  = self.mt_lexicon.get(dss_code, {})
                lex_key    = f'H{dss_code}'
                translit   = dss_entry.get('strongs_id', lex_key)
                disp_lemma = resolve_hebrew_lemma(dss_entry, dss_code)
                if lex_key not in self._page_lex:
                    self._page_lex[lex_key] = {
                        'bdb':     dss_entry.get('bdb_def',     ''),
                        'thb':     dss_entry.get('thb_def',     ''),
                        'strongs': dss_entry.get('strongs_def', ''),
                    }
                return (
                    f'<span class="{word_class}"'
                    f' data-lemma="{html.escape(disp_lemma)}"'
                    f' data-strongs="{html.escape(translit)}"'
                    f' data-scode="{html.escape(lex_key)}"'
                    f'{morph_thb_attrs(mai)}'
                    f' data-morph="{html.escape(morph)}"'
                    f' data-sf="{html.escape(surface)}"'
                    f'{_conc_attrs}'
                    f'>{surface}</span>'
                )
            return (
                f'<span class="{word_class}"'
                f' data-lemma="{html.escape(lemma_clean)}"'
                f'{morph_thb_attrs(mai)}'
                f' data-morph="{html.escape(morph)}"'
                f' data-sf="{html.escape(surface)}"'
                f'{_conc_attrs}'
                f'>{surface}</span>'
            )

    MAQQEF = '&#x05BE;'   # Hebrew maqqef as HTML entity — survives encoding/minification

    # Single-letter inseparable prefixes in biblical Hebrew (and Aramaic):
    # ב (in/at/with)  ה (the / interrogative)  ו (and)
    # כ (like/as)     ל (to/for)               מ (from, short form)
    # ש (that/which, mostly late BH)
    HEBREW_PREFIX_CONSONANTS = frozenset('בהוכלמש')

    def is_leading_prefix(self, word: Dict[str, Any]) -> bool:
        """
        True if this word is a single-letter Hebrew inseparable prefix that
        should attach directly to the following word (no space after it).

        Used for DSS and SP, which store prefix particles as standalone tokens
        without explicit 'attach' flags.  We detect by consonantal content.
        """
        # Prefer consonantal field; fall back to surface
        text = (word.get('consonantal') or word.get('surface') or '').strip()
        return len(text) == 1 and text in self.HEBREW_PREFIX_CONSONANTS

    def word_consonants(self, word: Dict[str, Any]) -> str:
        """
        Return the bare consonantal form of a word dict — no nikkud,
        no cantillation, no combining characters.
        """
        raw = (word.get('consonantal') or word.get('surface') or '').strip()
        nfd = unicodedata.normalize('NFD', raw)
        return ''.join(c for c in nfd if not unicodedata.combining(c))

    def extract_mt_maqqef_pairs(self, mt_words: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
        """
        Scan an MT word list and return the set of (a_consonants, b_consonants)
        pairs where MT uses a maqqef join at a *word boundary* (not a
        morphological prefix split that shares the same thb_id base).

        This set is used when rendering DSS/SP verses: wherever the same
        consonant pair appears we insert maqqef to match MT cantillation.
        """
        pairs: Set[Tuple[str, str]] = set()
        prev_w: Optional[Dict[str, Any]] = None
        prev_base: str = ''

        for w in mt_words:
            cons = self.word_consonants(w)
            if not cons or w.get('is_sof_pasuq'):
                continue

            if w.get('attach_with_maqqef') and prev_w is not None:
                curr_base = w.get('thb_id', '').rsplit('-', 1)[0]
                # Skip morphological splits (same thb_id root → prefix+stem)
                if not (prev_base and curr_base and prev_base == curr_base):
                    a = self.word_consonants(prev_w)
                    if a:
                        pairs.add((a, cons))

            prev_w = w
            prev_base = w.get('thb_id', '').rsplit('-', 1)[0] if w.get('thb_id') else ''

        return pairs

    def build_word_sequence(self, words: List[Dict[str, Any]], tradition: str,
                            mt_maqqef_pairs: Optional[Set[Tuple[str, str]]] = None) -> str:
        """
        Render a list of word dicts as a string of HTML spans, joining
        prefix particles and maqqef-linked words without intervening spaces.

        MT / DSS-with-flags path — flag is on the word that attaches to its predecessor:
          attach=True, attach_with_maqqef=False → direct join (no separator)
          attach=True, attach_with_maqqef=True  → insert maqqef ־ (no space)

        DSS / SP inference path — no flags in source data:
          1. If the current word is a single-letter inseparable prefix
             (ב ה ו כ ל מ ש), suppress the space *before the next* word.
          2. If mt_maqqef_pairs is supplied and the current (prev, curr)
             consonant pair appears in it, insert a maqqef instead of a space.

        Each word remains its own independent <span> so lemma data is intact.
        """
        parts: List[str] = []
        attach_next = False   # prefix lookahead: suppress space before the next span
        prev_cons: str = ''   # consonantal text of the last visible token
        prev_surface: str = ''  # bare surface of the last emitted token (for bracket suppression)

        for word in words:
            attach = word.get('attach', False)
            maqqef = word.get('attach_with_maqqef', False)

            span = self.build_word_span(word, tradition)
            if not span:
                continue

            curr_cons    = self.word_consonants(word)
            curr_surface = (word.get('surface') or '').strip()

            if parts:  # not the first visible token
                if attach:
                    # Explicit MT-style flag on this word
                    if maqqef:
                        # Stitch maqqef inside the preceding span so minifiers can't drop it
                        parts[-1] = parts[-1].replace('</span>', self.MAQQEF + '</span>', 1)
                    # else: direct join — no separator added
                elif attach_next:
                    # Previous word was a prefix (DSS/SP inference) — no space
                    pass
                elif (mt_maqqef_pairs is not None
                      and tradition in ('dss', 'sp')
                      and (prev_cons, curr_cons) in mt_maqqef_pairs):
                    # MT uses maqqef for this consonant pair — mirror it
                    parts[-1] = parts[-1].replace('</span>', self.MAQQEF + '</span>', 1)
                elif curr_surface == ']' or prev_surface == '[':
                    # Bracket-only tokens (KJV style): suppress the space so
                    # [ word ] renders as [word] rather than [ word ]
                    pass
                else:
                    parts.append(' ')

            parts.append(span)
            prev_cons    = curr_cons
            prev_surface = curr_surface

            # Update lookahead: was this word a prefix that should glue to the next?
            if tradition in ('dss', 'sp'):
                attach_next = self.is_leading_prefix(word)
            else:
                attach_next = False

        return ''.join(parts)

    def build_combined_verse_row(self, verse_num: int, verse_data_by_tradition: Dict[str, Any],
                                 vers_labels: Optional[Dict[str, str]] = None,
                                 aleppo_url: Optional[str] = None) -> str:
        """Build a verse row combining data from all available traditions."""
        traditions = ['mt', 'lxx', 'vul', 'sp', 'kjv', 'dss']
        if vers_labels is None:
            vers_labels = {}

        mt_verse = verse_data_by_tradition.get('mt')
        mt_maqqef_pairs: Set[Tuple[str, str]] = set()
        if mt_verse:
            mt_maqqef_pairs = self.extract_mt_maqqef_pairs(mt_verse.get('words', []))

        verse_html = f'<div class="verse-row" id="v{verse_num}" data-verse="{verse_num}">\n'

        for tradition in traditions:
            tradition_class = self.tradition_mapping[tradition]
            verse_html += f'    <div class="verse-cell {tradition_class}">\n'
            if tradition == 'mt' and aleppo_url:
                verse_html += f'        <div class="verse-number">{verse_num}<a class="ms-link" href="{aleppo_url}" target="_blank" rel="noopener">📜</a></div>\n'
            else:
                verse_html += f'        <div class="verse-number">{verse_num}</div>\n'
            if tradition in vers_labels:
                label = html.escape(vers_labels[tradition])
                verse_html += f'        <div class="vers-label">{label}</div>\n'

            verse_data = verse_data_by_tradition.get(tradition)

            if verse_data:
                if tradition == 'dss':
                    # Handle DSS scrolls
                    scrolls = verse_data.get('scrolls', [])
                    if scrolls:
                        scroll_data = scrolls[0]  # Use first scroll
                        scroll_name = scroll_data.get('scroll', 'DSS')
                        words = scroll_data.get('words', [])

                        # Collect unique fragment IDs in first-seen order
                        seen_frags: set = set()
                        frags = []
                        for w in words:
                            f = w.get('fragment', '')
                            if f and f not in seen_frags:
                                seen_frags.add(f)
                                frags.append(f)
                        frag_label = ' · ' + ', '.join(frags) if frags else ''

                        verse_html += f'        <div class="qumran-source">{scroll_name}{frag_label}</div>\n'
                        if words:
                            verse_html += '        <div class="verse-text">\n'
                            verse_html += '            ' + self.build_word_sequence(
                                words, tradition, mt_maqqef_pairs) + '\n'
                            verse_html += '        </div>\n'
                        else:
                            verse_html += '        <div class="verse-text gap">[lacuna]</div>\n'
                    else:
                        verse_html += '        <div class="verse-text gap">[lacuna]</div>\n'
                elif tradition == 'sp':
                    words = verse_data.get('words', [])
                    if words:
                        verse_html += '        <div class="verse-text">\n'
                        verse_html += '            ' + self.build_word_sequence(
                            words, tradition, mt_maqqef_pairs) + '\n'
                        verse_html += '        </div>\n'
                    else:
                        verse_html += '        <div class="verse-text gap">[no text]</div>\n'
                else:
                    # Other traditions (MT, LXX, VUL, KJV)
                    words = verse_data.get('words', [])
                    if words:
                        verse_html += '        <div class="verse-text">\n'
                        verse_html += '            ' + self.build_word_sequence(words, tradition) + '\n'
                        verse_html += '        </div>\n'
                    else:
                        verse_html += '        <div class="verse-text gap">[no text]</div>\n'
            else:
                verse_html += '        <div class="verse-text gap">[not available]</div>\n'

            verse_html += '    </div>\n'

        verse_html += '</div>\n'
        return verse_html
    
    def build_words_section(self, book: str, chapter: int) -> str:
        """Build the complete WORDS section for a chapter."""
        words_html = ""

        mt_book_name = self.get_tradition_book_name(book, 'mt')
        if 'mt' not in self.thb_data or mt_book_name not in self.thb_data['mt']:
            return '<div class="verse-row"><div class="verse-cell"><div class="verse-text">Chapter not available</div></div></div>'

        book_data = self.thb_data['mt'][mt_book_name]
        chapter_data = None
        for chap in book_data:
            if chap.get('chapter') == chapter:
                chapter_data = chap
                break

        if not chapter_data:
            return '<div class="verse-row"><div class="verse-cell"><div class="verse-text">Chapter not found</div></div></div>'

        # Pre-index each tradition's entire book by (ch, v) so the versification mapper
        # can resolve cross-chapter references (e.g. VUL Exo 8:1 appearing on MT Exo 7 page).
        trad_book_idx: Dict[str, Dict[Tuple[int, int], Any]] = {}
        for tradition in ('lxx', 'vul', 'sp', 'kjv', 'dss'):
            trad_book_name = self.get_tradition_book_name(book, tradition)
            if trad_book_name == 'NOT_AVAILABLE' or tradition not in self.thb_data:
                continue
            trad_book = self.thb_data[tradition].get(trad_book_name, [])
            idx: Dict[Tuple[int, int], Any] = {}
            for chap in trad_book:
                ch = chap.get('chapter')
                for v in chap.get('verses', []):
                    idx[(ch, v['verse'])] = v
            trad_book_idx[tradition] = idx

        for verse_data in chapter_data.get('verses', []):
            verse_num = verse_data.get('verse')
            combined_verse_data: Dict[str, Any] = {'mt': verse_data}
            vers_labels: Dict[str, str] = {}

            for tradition in ('lxx', 'vul', 'sp', 'kjv', 'dss'):
                idx = trad_book_idx.get(tradition)
                if idx is None:
                    continue

                if tradition in ('lxx', 'vul', 'kjv') and self.mapper:
                    result = self.mapper.resolve(tradition, book, chapter, verse_num)
                    if result is not None:
                        trad_refs, label = result
                        all_words: List[Any] = []
                        for (trad_ch, trad_v) in trad_refs:
                            v = idx.get((trad_ch, trad_v))
                            if v:
                                all_words.extend(v.get('words', []))
                        if all_words:
                            combined_verse_data[tradition] = {'verse': verse_num, 'words': all_words}
                            vers_labels[tradition] = label
                        continue  # skip default lookup below

                # Default: same chapter and verse as MT
                v = idx.get((chapter, verse_num))
                if v:
                    combined_verse_data[tradition] = v

            aleppo_url = self._aleppo_url(book, chapter, verse_num)
            words_html += self.build_combined_verse_row(
                verse_num, combined_verse_data, vers_labels, aleppo_url)

        return words_html
    
    # Canonical book name → word_frequency directory name.
    # The frequency files use Roman numerals with spaces; the builder
    # uses Arabic numerals without spaces internally.
    FREQ_DIR_NAME: Dict[str, str] = {
        '1Samuel':     'I Samuel',
        '2Samuel':     'II Samuel',
        '1Kings':      'I Kings',
        '2Kings':      'II Kings',
        '1Chronicles': 'I Chronicles',
        '2Chronicles': 'II Chronicles',
        'Song':        'Song of Solomon',
    }

    def build_chapter_frequency(self, book: str, chapter: int) -> Dict[str, Any]:
        """
        Merge verse-level word-frequency data for a chapter.
        Each verse maps Strong's code ("H1254") → { book: count, ... } for the
        entire Hebrew Bible.  All verse entries carry the same bible-wide totals
        for a given code, so first-occurrence wins.
        """
        freq: Dict[str, Any] = {}
        freq_book = self.FREQ_DIR_NAME.get(book, book)
        ch_data = self.concordance_data.get(freq_book, {}).get(str(chapter), {})
        for verse_data in ch_data.values():
            for key, val in verse_data.items():
                if key not in freq:
                    freq[key] = val
        return freq

    def build_word_database(self, book: str, chapter: int) -> str:
        """
        Bake per-chapter word-frequency data into the page as a JS object.
        Keys are Strong's codes ("H1254"); values are {Book: count} dicts.
        Zero network requests at runtime — pure static.
        """
        freq = self.build_chapter_frequency(book, chapter)
        return json.dumps(freq, ensure_ascii=False, separators=(',', ':'))

    def build_page_lexicon(self) -> str:
        """
        Serialize the per-page lexicon collected during build_words_section().
        Keys: 'H{code}' for MT, raw lemma for LXX, normalized lemma for VUL.
        Values: {bdb, ai} dicts.  Empty strings are omitted to save bytes.
        """
        compact = {
            k: {ik: iv for ik, iv in v.items() if iv}
            for k, v in self._page_lex.items()
        }
        return json.dumps(compact, ensure_ascii=False, separators=(',', ':'))

    # enrich_mt_word and enrich_lxx_word removed — definitions now read
    # directly from self.mt_lexicon / self.lxx_lexicon in build_word_span.

    def _prev_next_urls(self, book: str, chapter: int) -> tuple:
        """Return (prev_url, next_url) for a chapter page, wrapping at Bible boundaries."""
        book_list = list(self.chapter_counts.keys())
        book_idx = book_list.index(book)
        b_lower = book.lower()

        # Prev
        if chapter > 1:
            prev_url = f'/{b_lower}/{chapter - 1}/'
        elif book_idx > 0:
            prev_book = book_list[book_idx - 1]
            prev_ch = self.chapter_counts[prev_book]
            prev_url = f'/{prev_book.lower()}/{prev_ch}/'
        else:
            prev_url = f'/malachi/3/'  # wrap: before Genesis → end of Malachi

        # Next
        max_ch = self.chapter_counts[book]
        if chapter < max_ch:
            next_url = f'/{b_lower}/{chapter + 1}/'
        elif book_idx < len(book_list) - 1:
            next_book = book_list[book_idx + 1]
            next_url = f'/{next_book.lower()}/1/'
        else:
            next_url = '/genesis/1/'  # wrap: after Malachi 3 → Genesis 1

        return prev_url, next_url

    def generate_sitemap(self):
        """Write sitemap.xml listing all chapter pages and static pages."""
        domain = 'https://hebrewbible.dev'
        static_pages = ['about', 'license', 'privacy', 'terms']
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            # Site root (Genesis 1 copy) — highest priority
            f'  <url><loc>{domain}/</loc><priority>1.0</priority><changefreq>monthly</changefreq></url>',
        ]
        for slug in static_pages:
            lines.append(
                f'  <url><loc>{domain}/{slug}/</loc>'
                f'<priority>0.5</priority><changefreq>monthly</changefreq></url>'
            )
        for book, ch_count in self.chapter_counts.items():
            b_lower = book.lower()
            for ch in range(1, ch_count + 1):
                lines.append(
                    f'  <url><loc>{domain}/{b_lower}/{ch}/</loc>'
                    f'<priority>0.8</priority><changefreq>monthly</changefreq></url>'
                )
        lines.append('</urlset>')
        sitemap_path = Path(self.output_dir) / 'sitemap.xml'
        with open(sitemap_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        chapter_count = len(lines) - 3 - len(static_pages)
        print(f'  Sitemap: {sitemap_path} ({chapter_count} chapters + {len(static_pages)} static pages)')

    def build_chapter_page(self, book: str, chapter: int) -> str:
        """Build a complete chapter page."""
        # Reset per-page lexicon so each chapter starts fresh
        self._page_lex: dict = {}

        # Replace all template tags
        html = self.template
        
        # Basic info
        book_display = self.get_book_display_name(book)
        passage = f"{book_display} {chapter}"
        book_lowercase = book.lower()
        
        # Determine which columns to show
        torah_books = ['Genesis', 'Exodus', 'Leviticus', 'Numbers', 'Deuteronomy']
        sp_available = book in torah_books
        
        # DSS availability - add more books that lack DSS coverage as discovered
        dss_missing_books = ['Esther', '1Chronicles', '2Chronicles']
        dss_available = book not in dss_missing_books
        
        # Build CSS classes for hiding columns
        body_classes = []
        if not sp_available:
            body_classes.append('hide-sp')
        if not dss_available:
            body_classes.append('hide-dss')

        body_classes_str = ' '.join(body_classes)

        # Layout context for JS preference isolation
        if sp_available:
            layout_ctx = 'torah'
        elif not dss_available:
            layout_ctx = 'nodss'
        else:
            layout_ctx = 'standard'

        # Prev / next chapter URLs (baked as real hrefs for Google link-following)
        prev_url, next_url = self._prev_next_urls(book, chapter)

        # All replacements
        replacements = {
            '{{{PASSAGE}}}': passage,
            '{{{BOOK}}}': book_display,
            '{{{BOOK_LOWERCASE}}}': book_lowercase,
            '{{{CHAPTER_NUMBER}}}': str(chapter),
            '{{{DOMAIN}}}': 'hebrewbible.dev',
            '{{{BODY_CLASSES}}}': body_classes_str,
            '{{{LAYOUT_CTX}}}': layout_ctx,
            '{{{PREV_URL}}}': prev_url,
            '{{{NEXT_URL}}}': next_url,
            '{{{WORDS}}}': self.build_words_section(book, chapter),  # must be first — populates _page_lex
            '{{{WORD_DATABASE}}}': self.build_word_database(book, chapter),
            '{{{LEX_DATA}}}': self.build_page_lexicon(),
        }
        
        for tag, replacement in replacements.items():
            html = html.replace(tag, replacement)
        
        return html
    
    def create_directory_structure(self, book: str, chapter: int):
        """Create the output directory structure."""
        book_dir = Path(self.output_dir) / book.lower() / str(chapter)
        book_dir.mkdir(parents=True, exist_ok=True)
        return book_dir
    
    def build_site(self):
        """Build the complete site for all books."""
        print("Starting full Bible build...")
        
        self.load_data()
        
        total_chapters = 0
        books_built = 0
        
        # Process each book
        for book, chapter_count in self.chapter_counts.items():
            print(f"\nBuilding {book} ({chapter_count} chapters)...")
            
            # Check if book exists in MT data (our reference)
            mt_book_name = self.get_tradition_book_name(book, 'mt')
            
            if 'mt' not in self.thb_data or mt_book_name not in self.thb_data['mt']:
                print(f"  Warning: {mt_book_name} not found in MT data, trying alternative names...")
                
                # Try alternative book names
                alternatives = [book, book.replace(' ', ''), book.replace(' ', '_')]
                if book == 'Song':
                    alternatives.extend(['Song of Songs', 'Song of Solomon', 'SongOfSongs'])
                elif book.startswith('1') or book.startswith('2'):
                    # Try with and without spaces
                    alternatives.append(book.replace(' ', ''))
                    alternatives.append(book.replace('Samuel', ' Samuel').replace('Kings', ' Kings').replace('Chronicles', ' Chronicles'))
                
                found = False
                for alt_name in alternatives:
                    if alt_name in self.thb_data['mt']:
                        print(f"  Found {book} as '{alt_name}' in data")
                        mt_book_name = alt_name
                        found = True
                        break
                
                if not found:
                    print(f"  Skipping {book} - not found in any format")
                    continue
            
            # Build each chapter
            for chapter in range(1, chapter_count + 1):
                print(f"  Building {book} {chapter}...")
                
                # Create directory
                output_dir = self.create_directory_structure(book, chapter)
                
                # Build page
                html = self.build_chapter_page(book, chapter)
                
                # Write file
                output_file = output_dir / 'index.html'
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(html)
                
                total_chapters += 1
            
            books_built += 1
            print(f"  Completed {book}")
        
        print(f"\nFull Bible build complete!")
        print(f"Generated {books_built} books, {total_chapters} total chapters.")
        self.generate_sitemap()
        self.build_concordance_stubs()

    def build_mini(self, book: str = 'Genesis', chapters: int = 3,
                   concordance: bool = False):
        """Build a small slice for fast preview (default: Genesis 1-3)."""
        print(f"Mini build: {book} chapters 1-{chapters}...")
        self.load_data()
        for chapter in range(1, chapters + 1):
            print(f"  Building {book} {chapter}...")
            output_dir = self.create_directory_structure(book, chapter)
            html_out = self.build_chapter_page(book, chapter)
            with open(output_dir / 'index.html', 'w', encoding='utf-8') as f:
                f.write(html_out)
        print(f"Mini build complete. Preview at thb/public_html/{book.lower()}/1/")

        if concordance:
            # Build concordance stubs only for lemmas that appear in the
            # chapters we just built — a small, testable subset.
            mini_keys: Dict[str, Set[str]] = {}
            for tradition, index in self.occurrence_index.items():
                for key, occurrences in index.items():
                    for occ_book, ch, _ in occurrences:
                        if occ_book == book and ch <= chapters:
                            mini_keys.setdefault(tradition, set()).add(key)
                            break
            total = sum(len(v) for v in mini_keys.values())
            print(f"\nBuilding mini concordance ({total} lemmas from {book} 1-{chapters})...")
            out_base = Path(self.output_dir) / 'concordance'
            written = 0
            hapax_skipped = 0
            for tradition, keys in mini_keys.items():
                hapax = self.hapax.get(tradition, set())
                for key in keys:
                    if key in hapax:
                        hapax_skipped += 1
                        continue
                    occurrences = self.occurrence_index[tradition][key]
                    stub_dir = out_base / tradition / _safe_dirname(key)
                    stub_dir.mkdir(parents=True, exist_ok=True)
                    page = self.build_concordance_stub_page(tradition, key, occurrences)
                    (stub_dir / 'index.html').write_text(page, encoding='utf-8')
                    written += 1
            print(f"  {written} stubs written, {hapax_skipped} hapax skipped.")

    def build_search_pages(self, temp_dir: str):
        """Build one stripped HTML page per chapter for Pagefind indexing.

        Each verse is a heading+paragraph pair. Pagefind auto-detects h3
        elements as anchors and returns sub_results with #v{n} URLs.
        Pages are never served — they exist only for the index.
        """
        temp_path = Path(temp_dir)
        if temp_path.exists():
            shutil.rmtree(temp_path)

        print("Building Pagefind index pages (stripped)...")
        count = 0

        for book, chapter_count in self.chapter_counts.items():
            mt_book_name = self.get_tradition_book_name(book, 'mt')
            if 'mt' not in self.thb_data or mt_book_name not in self.thb_data['mt']:
                continue
            mt_book_data = self.thb_data['mt'][mt_book_name]

            for chapter in range(1, chapter_count + 1):
                chapter_data = next(
                    (c for c in mt_book_data if c.get('chapter') == chapter), None
                )
                if not chapter_data:
                    continue

                book_display = self.get_book_display_name(book)
                passage      = f"{book_display} {chapter}"
                sections     = []

                for verse_data in chapter_data.get('verses', []):
                    verse_num = verse_data.get('verse')
                    parts     = []

                    for tradition in ('mt', 'lxx', 'vul', 'kjv', 'dss'):
                        trad_book_name = self.get_tradition_book_name(book, tradition)
                        if trad_book_name == 'NOT_AVAILABLE':
                            continue
                        trad_data = self.thb_data.get(tradition, {})
                        trad_book = trad_data.get(trad_book_name)
                        if not trad_book:
                            continue
                        trad_chapter = next(
                            (c for c in trad_book if c.get('chapter') == chapter), None
                        )
                        if not trad_chapter:
                            continue
                        verse = next(
                            (v for v in trad_chapter.get('verses', [])
                             if v.get('verse') == verse_num), None
                        )
                        if not verse:
                            continue

                        if tradition == 'mt':
                            # Output joined orthographic words (e.g. השמים) so
                            # prefixed forms are searchable, plus each attach
                            # token separately (e.g. שמים) so bare roots also
                            # match. Both forms end up in the same paragraph.
                            joined = []
                            roots  = []
                            buf = ''
                            for w in verse.get('words', []):
                                if w.get('is_sof_pasuq'):
                                    continue
                                cons = (w.get('consonantal') or '').strip()
                                if not cons:
                                    continue
                                if w.get('attach'):
                                    buf += cons
                                    roots.append(cons)
                                else:
                                    if buf:
                                        joined.append(buf)
                                    buf = cons
                            if buf:
                                joined.append(buf)
                            text = ' '.join(joined)
                            if roots:
                                text += ' ' + ' '.join(roots)
                        else:
                            words = [
                                normalize_for_search(w.get('surface', ''), tradition)
                                for w in verse.get('words', [])
                                if w.get('surface') and not w.get('is_sof_pasuq')
                            ]
                            text = ' '.join(w for w in words if w)
                        if text:
                            parts.append(text)

                    if parts:
                        # h3 with id="v{n}" — Pagefind auto-detects headings as
                        # anchors and returns sub_results with URL /chapter/#v{n}
                        sections.append(
                            f'<h3 id="v{verse_num}">{verse_num}</h3>'
                            f'<p>{" ".join(parts)}</p>'
                        )

                if not sections:
                    continue

                page = (
                    f'<!DOCTYPE html><html lang="en"><head>'
                    f'<meta charset="UTF-8"><title>{passage} — THB</title></head>'
                    f'<body>'
                    f'<span data-pagefind-meta="passage:{passage}" hidden></span>'
                    + '\n'.join(sections) +
                    f'</body></html>'
                )

                out_dir = temp_path / book.lower() / str(chapter)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / 'index.html').write_text(page, encoding='utf-8')
                count += 1

        print(f"  {count} chapter pages written.")

def main():
    parser = argparse.ArgumentParser(description='THB Site Builder')
    parser.add_argument('--mini', action='store_true',
                        help='Fast preview build: Genesis 1-3 only, skips Pagefind')
    parser.add_argument('--book', default='Genesis',
                        help='Book to use for --mini (default: Genesis)')
    parser.add_argument('--chapters', type=int, default=3,
                        help='Number of chapters for --mini (default: 3)')
    parser.add_argument('--concordance', action='store_true',
                        help='With --mini: also build concordance stubs for lemmas in those chapters')
    args = parser.parse_args()

    _here = Path(__file__).parent
    backend_dir   = str(_here / 'backend')
    output_dir    = str(_here / 'public_html')
    template_path = str(_here / 'thb_template_14.html')

    for file_path in [backend_dir, template_path]:
        if not os.path.exists(file_path):
            print(f"Error: Required path not found: {file_path}")
            return

    builder = THBSiteBuilder(backend_dir, template_path, output_dir)

    if args.mini:
        builder.build_mini(book=args.book, chapters=args.chapters,
                           concordance=args.concordance)
    else:
        builder.build_site()

        # Copy Genesis 1 as the site root index
        gen1 = Path(output_dir) / 'genesis' / '1' / 'index.html'
        root_index = Path(output_dir) / 'index.html'
        if gen1.exists():
            shutil.copy2(str(gen1), str(root_index))
            print("Copied genesis/1/index.html -> index.html")

        temp_dir = str(_here / '.search_build')
        builder.build_search_pages(temp_dir)

        print("\nRunning Pagefind on stripped index pages...")
        result = subprocess.run(
            f'npx --yes pagefind --site "{temp_dir}"',
            capture_output=False, shell=True
        )
        if result.returncode != 0:
            print(f"Pagefind exited with code {result.returncode} — index may be incomplete.")
        else:
            pagefind_src = Path(temp_dir) / 'pagefind'
            pagefind_dst = Path(output_dir) / 'pagefind'
            if pagefind_dst.exists():
                shutil.rmtree(str(pagefind_dst))
            shutil.copytree(str(pagefind_src), str(pagefind_dst))
            shutil.rmtree(temp_dir)
            print("Pagefind index built and installed.")

if __name__ == '__main__':
    main()