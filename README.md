# Translator's Hebrew Bible (THB)

![version](https://img.shields.io/badge/version-1.5.0626-c9b037?style=flat-square)

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

### Lemma Rarity Coloring

The ◈ button (top-right of each chapter page) activates rarity coloring. A logarithmic sensitivity slider controls the threshold at which a lemma begins to receive color. Lemmas above the threshold are uncolored. Below it, the color ramps from gold toward red as frequency decreases — using the same palette as DSS damage rendering.

| Color | Meaning |
|-------|---------|
| Gold `#c8b040` | Uncommon — approaching the threshold |
| Amber `#c07830` | Rare |
| Orange `#b05020` | Very rare |
| Red `#a03018` | Extremely rare or hapax legomenon |

Frequency data (`pageFreq`) is baked into each chapter page at build time — one `{h, b}` entry per unique lemma (HB total and current-book total). No network request is made at interaction time.

### Cross-Tradition Alignment

Hover any word and two layers of semantic alignment activate simultaneously across all six columns:

| Highlight | Meaning |
|-----------|---------|
| Light gold background | **Thought unit** — the same semantic idea across traditions |
| Full gold background | **Word counterpart** — the exact lexical equivalent |


### DSS Damage Rendering

The DSS column encodes the physical condition of each letter using color:

| Color | Meaning |
|-------|---------|
| Greyed out | Reconstructed — letter restored from context inside `[ ]` |
| Muted gold | Uncertain reading (`?`) |
| Amber | Damaged letter (`#`) |
| Orange | Damaged and uncertain (`#?`) |
| Red-orange | Severely damaged (`##`) |
| Strikethrough | Scribal cancellation — deleted by the original scribe |
| Blue superscript | Supralinear insertion — added above the line |
| Green | Ancient correction |
| Purple | Modern editorial correction |

The toggle button in the DSS column header strips all damage markup, leaving plain consonantal text.

---

## How It Works

THB is a **build-once, serve-forever** static site. The builder reads structured JSON data from `backend/`, fills a single HTML template, and writes one `index.html` per book/chapter.

```
thb/
├── thb_builder.py       ← site builder (~1,800 lines)
├── thb_template.html    ← HTML/CSS/JS shell (all inline, no external deps)
├── backend/
│   ├── thb.mt.json              ← Hebrew text + morphology + full punctuation
│   ├── thb.lxx.json             ← Greek text + morphology
│   ├── thb.vul.json             ← Latin text + morphology
│   ├── thb.sp.json              ← Samaritan Pentateuch
│   ├── thb.kjv.json             ← English text + Strong's alignment
│   ├── thb.dss.json             ← Dead Sea Scrolls
│   ├── thb.lexicon.mt.json      ← MT lexicon (12,321 entries)
│   ├── thb.lexicon.lxx.json     ← LXX lexicon (18,248 entries)
│   ├── thb.lexicon.vul.json     ← VUL lexicon (11,704 entries)
│   ├── thb.versification.json   ← Cross-tradition verse mapping
│   ├── thb.aleppo.json          ← Aleppo Codex facsimile page index
│   ├── oshb_cache/              ← Cached OSHB MorphHB XML files (39 books)
│   ├── concordance/             ← Per-book Strong's frequency data (39 files)
│   └── alignment/
│       ├── thought/wlc/         ← Thought-unit alignment, one JSON per book/chapter
│       └── word/wlc/            ← Word-level alignment, one JSON per book/chapter
└── public_html/                 ← Builder output
    ├── static/                      ← Fonts, icons, images (committed)
    ├── about/ license/ privacy/ terms/ search/  ← Static pages (committed)
    └── genesis/1/ exodus/1/ ...     ← Generated chapter pages (not committed)
```

Concordance stub pages (~47K files across 6 traditions) exceed Cloudflare Pages' 20K-file limit and are deployed to six dedicated projects instead — see **Deployment** below.

---

## Building

**Requirements:** Python 3 (stdlib only). Node.js / `npx` required for Pagefind search indexing.

```bash
# Full build — all 39 books, all chapters + concordance pages
python thb/thb_builder.py --concordance

# Fast preview — Genesis 1–3 only
python thb/thb_builder.py --mini

# Preview with concordance stubs (Genesis 1–3)
python thb/thb_builder.py --mini --concordance
```

Output goes to `public_html/`. The full architecture is documented in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Deployment

The main site and concordance are deployed as separate Cloudflare Pages projects because concordance stub pages (~47K files across 6 traditions) exceed the Pages 20K-file limit.

**Main site** — `public_html` minus the `concordance/` subdirectory (exceeds 20K limit):

```bash
python -c "import shutil,os; d='public_html_deploy'; os.path.exists(d) and shutil.rmtree(d); shutil.copytree('public_html', d, ignore=shutil.ignore_patterns('concordance'))"
npx wrangler pages deploy public_html_deploy --project-name thb --branch main
python -c "import shutil; shutil.rmtree('public_html_deploy')"
```

**Concordance** — one project per tradition, deployed from `public_html/concordance/{tradition}/`:

```bash
npx wrangler pages deploy public_html/concordance/mt  --project-name thb-concordance-mt  --branch main
npx wrangler pages deploy public_html/concordance/lxx --project-name thb-concordance-lxx --branch main
npx wrangler pages deploy public_html/concordance/vul --project-name thb-concordance-vul --branch main
npx wrangler pages deploy public_html/concordance/sp  --project-name thb-concordance-sp  --branch main
npx wrangler pages deploy public_html/concordance/kjv --project-name thb-concordance-kjv --branch main
npx wrangler pages deploy public_html/concordance/dss --project-name thb-concordance-dss --branch main
```

| Tradition | Project | Lemmas |
|-----------|---------|--------|
| MT | thb-concordance-mt.pages.dev | 8,632 |
| LXX | thb-concordance-lxx.pages.dev | 11,775 |
| VUL | thb-concordance-vul.pages.dev | 11,704 |
| SP | thb-concordance-sp.pages.dev | 2,716 |
| KJV | thb-concordance-kjv.pages.dev | 8,582 |
| DSS | thb-concordance-dss.pages.dev | 2,772 |

Concordance links in the chapter pages point directly to the appropriate `*.pages.dev` subdomain.

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
