# Translator's Hebrew Bible (THB)

**Powerful, free data and tooling for the Hebrew Bible.**

THB is a structured, open dataset covering six major textual traditions of the Hebrew Bible — with word-level morphology and lexical definitions developed in-house and released under MIT license, blended with the best available open scholarly resources. The included builder turns that data into a fully static hexapla site that runs entirely in the browser — hover any word and get the data.

**Live site:** [hebrewbible.dev](https://hebrewbible.dev)

---

## The Six Traditions

| Column | Tradition | Source |
|--------|-----------|--------|
| **MT** | Hebrew Masoretic Text | Westminster Leningrad Codex (OSHB/MorphHB) |
| **LXX** | Greek Septuagint | Swete 1930 — Codex Vaticanus recension |
| **VUL** | Latin Vulgate | Clementine Vulgate |
| **KJV** | English (King James Version) | 1769 text; Strong's alignment by Eliran Wong |
| **DSS** | Dead Sea Scrolls | ETCBC/Naaijer — Abegg transcriptions |
| **SP** | Samaritan Pentateuch | DT-UCPH Text-Fabric dataset |

Every word carries: lemma, morphological parsing, Strong's number (where applicable), and inline lexical definitions from BDB (Hebrew), LSJ (Greek), and Lewis & Short (Latin).

---

## How It Works

THB is a **build-once, serve-forever** static site. The builder reads structured JSON data from `backend/`, fills a single HTML template, and writes one `index.html` per book/chapter (928 pages total).
```
thb/
├── thb_builder.py      ← the entire site builder (~1,650 lines)
├── thb_complete_template.html ← HTML/CSS/JS shell (all inline, no external deps)
├── backend/
│   ├── thb.1.3.mt.json       ← Hebrew text + morphology
│   ├── thb.1.3.lxx.json      ← Greek text + morphology
│   ├── thb.1.3.vul.json      ← Latin text + morphology
│   ├── thb.1.3.sp.json       ← Samaritan Pentateuch
│   ├── thb.1.3.kjv.json      ← English text + Strong's alignment
│   ├── thb.1.3.dss.json      ← Dead Sea Scrolls
│   ├── thb.1.3.lexicon.json  ← Unified lexicon: MT/LXX/VUL definitions
│   ├── thb.1.3.versification.json ← Cross-tradition verse mapping
│   ├── thb.1.3.aleppo.json   ← Aleppo Codex facsimile page index (587 pages)
│   └── concordance/          ← Per-book Strong's frequency data (39 files)
└── public_html/              ← Builder output
    ├── static/               ← Fonts, icons, images (committed)
    ├── about/ license/ privacy/ terms/ search/  ← Static pages (committed)
    └── genesis/1/ exodus/1/ ...  ← Generated chapter pages (not committed)
```

---

## Building

**Requirements:** Python 3 (stdlib only). Node.js / `npx` required for Pagefind search indexing.

```bash
# Full build — all 39 books, 928 chapters, Pagefind index
python thb/thb_builder.py

# Fast preview — Genesis 1–3 only, skips Pagefind
python thb/thb_builder.py --mini

# Preview a specific book/chapter count
python thb/thb_builder.py --mini --book Nehemiah --chapters 3
```

Output goes to `public_html/`. The root `index.html` is a copy of `genesis/1/index.html`. The full architecture is documented in [ARCHITECTURE.md](ARCHITECTURE.md).


---

## Data & Licensing

The **platform software** (builder, template, tooling) is © 2026 Michael Muzar, released under the **MIT License**.

The underlying text and morphology data carries mixed licensing from its upstream sources:

| Source | License |
|--------|---------|
| Westminster Leningrad Codex (MT text) | Public Domain |
| OpenScriptures Hebrew morphology | CC-BY 4.0 |
| Swete LXX (via Eliran Wong) | GPL 3.0 |
| Clementine Vulgate | Public Domain |
| Samaritan Pentateuch — DT-UCPH | CC-BY 4.0 |
| KJV text | Public Domain |
| OpenHebrewBible KJV–Strong's alignment | CC-BY-NC 4.0 |
| Dead Sea Scrolls — ETCBC/Naaijer | CC-BY-NC 4.0 |
| BDB Enhanced lexicon — unfoldingWord | Public Domain + CC-BY 4.0 |
| LSJ Greek Lexicon — Perseus | CC-BY-SA 4.0 |
| Lewis & Short Latin Dictionary — Perseus | CC-BY-SA 4.0 |
| Copenhagen Alliance versification data | CC-BY-SA 4.0 |

Full attribution and source links: [hebrewbible.dev/license](https://hebrewbible.dev/license)

**Note on redistribution:** The platform MIT license applies to the code. If you redistribute the data, the most restrictive upstream license that applies is GPL 3.0 (LXX) for full-tradition distributions, and CC-BY-NC 4.0 (DSS, KJV alignment) for non-commercial use only.

---

## Issues & Contact

Found a bug, data error, or have a question? Open an issue on this repo.

For licensing or attribution questions: support AT hebrewbible.dev
