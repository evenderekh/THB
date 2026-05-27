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

THB is a **build-once, serve-forever** static site. The builder reads structured JSON data from `backend/`, fills a single HTML template, and writes one `index.html` per book/chapter.

```
thb/
├── thb_builder_14.py   ← site builder v1.4 (~1,700 lines)
├── thb_template_14.html ← HTML/CSS/JS shell (all inline, no external deps)
├── backend/
│   ├── thb.1.3.mt.json              ← Hebrew text + morphology
│   ├── thb.1.3.lxx.json             ← Greek text + morphology
│   ├── thb.1.3.vul.json             ← Latin text + morphology
│   ├── thb.1.3.sp.json              ← Samaritan Pentateuch
│   ├── thb.1.3.kjv.json             ← English text + Strong's alignment
│   ├── thb.1.3.dss.json             ← Dead Sea Scrolls
│   ├── thb.1.4.lexicon.mt.json      ← MT lexicon (12,321 entries)
│   ├── thb.1.4.lexicon.lxx.json     ← LXX lexicon (18,248 entries)
│   ├── thb.1.4.lexicon.vul.json     ← VUL lexicon (11,704 entries)
│   ├── thb.1.3.versification.json   ← Cross-tradition verse mapping
│   ├── thb.1.3.aleppo.json          ← Aleppo Codex facsimile page index
│   └── concordance/                 ← Per-book Strong's frequency data (39 files)
└── public_html/                     ← Builder output
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
python thb/thb_builder_14.py --concordance

# Fast preview — Genesis 1–3 only
python thb/thb_builder_14.py --mini

# Preview with concordance stubs (Genesis 1–3)
python thb/thb_builder_14.py --mini --concordance
```

Output goes to `public_html/`. The full architecture is documented in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Deployment

The main site and concordance are deployed as separate Cloudflare Pages projects because concordance stub pages (~47K files across 6 traditions) exceed the Pages 20K-file limit.

**Main site** — `npx wrangler pages deploy public_html --project-name thb`

**Concordance** — one project per tradition, deployed from `public_html/concordance/{tradition}/`:

```bash
npx wrangler pages deploy public_html/concordance/mt  --project-name thb-concordance-mt
npx wrangler pages deploy public_html/concordance/lxx --project-name thb-concordance-lxx
npx wrangler pages deploy public_html/concordance/vul --project-name thb-concordance-vul
npx wrangler pages deploy public_html/concordance/sp  --project-name thb-concordance-sp
npx wrangler pages deploy public_html/concordance/kjv --project-name thb-concordance-kjv
npx wrangler pages deploy public_html/concordance/dss --project-name thb-concordance-dss
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

## Version 1.4 — What's New

- **Concordance**: every lemma links to a stub page listing all occurrences across the Bible, grouped by book in collapsible dropdowns that show the full verse text.
- **Hapax legomena**: words appearing in exactly one verse are badged in the hover panel.
- **DSS damage-marker rendering**: `surface_full` sigils (`[ ]` lacunae, `#`/`?` uncertainty, `{{ }}` cancellations, supralinear insertions, ancient/modern corrections) are parsed at page load into color-coded spans with cross-word bracket-state tracking.
- **Split lexicons**: the unified `thb.1.3.lexicon.json` is replaced by three smaller per-tradition files for faster loading.
- **Sticky header scroll fix**: `ResizeObserver` keeps `--top-bar-h` in sync so anchor links always land with the verse fully visible.

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
