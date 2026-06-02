"""
wlc_inventory.py
Inventory completeness of thb/backend/alignment/word/wlc/ against thb.mt.json.

Reports:
  1. Chapter-level completeness per book
  2. Verse-level completeness per book (summary + detail)
  3. Token translation coverage per version, context-aware for SP/DSS
"""

import json
import os
import re
from collections import defaultdict

MT_JSON  = os.path.join(os.path.dirname(__file__), "..", "..", "thb.mt.json")
WLC_DIR  = os.path.join(os.path.dirname(__file__), "wlc")
VERSIONS = ["LXX", "VUL", "SP", "KJV", "DSS"]

# SP only attested for Pentateuch; DSS coverage is fragmentary across the canon.
# For fill-rate purposes, exclude a version from a book's denominator when that
# version simply has no witness for that book (SP outside Torah = N/A).
SP_BOOKS  = {"Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy"}


def load_mt_index(path):
    with open(path, encoding="utf-8") as f:
        mt = json.load(f)
    index = {}
    for book, chapters in mt.items():
        index[book] = {}
        for ch_obj in chapters:
            ch = ch_obj["chapter"]
            index[book][ch] = {}
            for v_obj in ch_obj.get("verses", []):
                index[book][ch][v_obj["verse"]] = len(v_obj.get("words", []))
    return index


BOOK_ALIASES = {
    "Song of Solomon": "Song of Songs",
}

def parse_wlc_filename(name):
    m = re.match(r"^(.+)\.(\d+)\.json$", name)
    if m:
        book = m.group(1).replace("_", " ")
        return BOOK_ALIASES.get(book, book), int(m.group(2))
    return None


def load_wlc_files(wlc_dir):
    result = defaultdict(dict)
    for fname in os.listdir(wlc_dir):
        parsed = parse_wlc_filename(fname)
        if not parsed:
            continue
        book, ch = parsed
        path = os.path.join(wlc_dir, fname)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        result[book][ch] = {int(v) for v in data.keys()}
    return result


def count_tokens_and_nulls(wlc_dir):
    total = 0
    nulls = defaultdict(int)
    book_nulls  = defaultdict(lambda: defaultdict(int))
    book_totals = defaultdict(int)

    for fname in sorted(os.listdir(wlc_dir)):
        parsed = parse_wlc_filename(fname)
        if not parsed:
            continue
        book, _ = parsed
        path = os.path.join(wlc_dir, fname)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for verse_groups in data.values():
            for group in verse_groups:
                for tok in group.get("tokens", []):
                    total += 1
                    book_totals[book] += 1
                    for v in VERSIONS:
                        val = tok.get(v)
                        if val is None or (isinstance(val, str) and val.strip() == ""):
                            nulls[v] += 1
                            book_nulls[book][v] += 1

    return total, nulls, book_totals, book_nulls


def fmt_pct(p):
    return f"{p:.0f}%"


def main():
    import sys
    md = "--md" in sys.argv
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_file = positional[0] if len(positional) > 0 else None
    wlc_dir_override = positional[1] if len(positional) > 1 else None
    global WLC_DIR
    if wlc_dir_override:
        WLC_DIR = wlc_dir_override

    print("Loading thb.mt.json …",    file=sys.stderr)
    mt = load_mt_index(MT_JSON)
    print("Scanning wlc directory …", file=sys.stderr)
    wlc = load_wlc_files(WLC_DIR)
    print("Counting tokens …",        file=sys.stderr)
    total_tokens, nulls, book_totals, book_nulls = count_tokens_and_nulls(WLC_DIR)

    lines = []
    w = lines.append

    # ── header ────────────────────────────────────────────────────────────────
    if md:
        w("# WLC Alignment Inventory\n")
        w(f"*Generated {__import__('datetime').date.today()}*\n")

    # ── 1. Chapter completeness ───────────────────────────────────────────────
    if md:
        total_books_ok = sum(1 for b in mt if not (set(mt[b]) - set(wlc.get(b, {}))))
        w("## 1. Chapter Files\n")
        w(f"**{total_books_ok}/{len(mt)}** books fully present.\n")
        w("| Book | Chapters | % | Missing |")
        w("|------|:--------:|--:|---------|")
    else:
        w("=" * 70)
        w("1. CHAPTER FILES")
        w("=" * 70)

    for book in sorted(mt):
        exp  = set(mt[book].keys())
        pres = set(wlc.get(book, {}).keys())
        miss = sorted(exp - pres)
        pct  = 100 * len(pres) / len(exp) if exp else 0
        if md:
            miss_str = ", ".join(str(c) for c in miss) if miss else "—"
            w(f"| {book} | {len(pres)}/{len(exp)} | {pct:.0f}% | {miss_str} |")
        else:
            status = "OK" if not miss else f"MISSING {miss}"
            w(f"  {book:<22} {len(pres):>3}/{len(exp):<3} ({pct:5.1f}%)  {status}")

    if not md:
        total_books_ok = sum(1 for b in mt if not (set(mt[b]) - set(wlc.get(b, {}))))
        w(f"\nBooks fully present: {total_books_ok}/{len(mt)}")

    # ── 2. Verse completeness ─────────────────────────────────────────────────
    # Build per-book verse stats
    book_verse_exp  = defaultdict(int)
    book_verse_pres = defaultdict(int)
    gap_rows = []  # (book, ch, missing_vs_list)

    for book in sorted(mt):
        for ch in sorted(mt[book]):
            exp_vs  = set(mt[book][ch].keys())
            pres_vs = wlc.get(book, {}).get(ch, set())
            book_verse_exp[book]  += len(exp_vs)
            book_verse_pres[book] += len(exp_vs & pres_vs)
            miss_vs = sorted(exp_vs - pres_vs)
            if miss_vs:
                gap_rows.append((book, ch, miss_vs))

    if md:
        w("\n## 2. Verse Coverage\n")
        w("### Summary by book\n")
        w("| Book | Verses present | Total | % |")
        w("|------|:--------------:|------:|--:|")
    else:
        w("\n" + "=" * 70)
        w("2. VERSE COVERAGE")
        w("=" * 70)
        w(f"\n  {'Book':<22}  {'Present':>10}  {'Total':>7}  {'%':>6}")
        w(f"  {'-'*22}  {'-'*10}  {'-'*7}  {'-'*6}")

    for book in sorted(mt):
        exp  = book_verse_exp[book]
        pres = book_verse_pres[book]
        pct  = 100 * pres / exp if exp else 0
        if md:
            w(f"| {book} | {pres:,} | {exp:,} | {pct:.0f}% |")
        else:
            w(f"  {book:<22}  {pres:>10,}  {exp:>7,}  {pct:>5.1f}%")

    total_exp  = sum(book_verse_exp.values())
    total_pres = sum(book_verse_pres.values())
    overall    = 100 * total_pres / total_exp if total_exp else 0
    if md:
        w(f"| **Total** | **{total_pres:,}** | **{total_exp:,}** | **{overall:.0f}%** |")
        w(f"\n### Detail — missing verses per chapter\n")
        w("| Book | Ch | Missing verses |")
        w("|------|----|----------------|")
        for book, ch, miss_vs in gap_rows:
            w(f"| {book} | {ch} | {', '.join(str(v) for v in miss_vs)} |")
    else:
        w(f"\n  {'TOTAL':<22}  {total_pres:>10,}  {total_exp:>7,}  {overall:>5.1f}%")
        w("\n  Detail — missing verses per chapter:")
        for book, ch, miss_vs in gap_rows:
            w(f"  {book} {ch}: {miss_vs}")

    # ── 3. Token translation coverage ─────────────────────────────────────────
    if md:
        w("\n## 3. Token Translation Coverage\n")
        w(f"**{total_tokens:,}** tokens across all present files.\n")
        w("> SP fill % is computed only over Pentateuch books (its sole witness corpus).\n")
        w("| Version | Filled | Null | Fill % |")
        w("|---------|-------:|-----:|-------:|")
    else:
        w("\n" + "=" * 70)
        w("3. TOKEN TRANSLATION COVERAGE")
        w("=" * 70)
        w(f"\n  Total tokens: {total_tokens:,}")
        w("  (SP fill % scoped to Pentateuch only)\n")
        w(f"  {'Version':<8}  {'Filled':>10}  {'Null':>8}  {'Fill %':>7}")
        w(f"  {'-'*8}  {'-'*10}  {'-'*8}  {'-'*7}")

    # SP: denominator = tokens in SP_BOOKS only
    sp_total = sum(book_totals[b] for b in book_totals if b in SP_BOOKS)
    sp_null  = sum(book_nulls[b]["SP"] for b in book_nulls if b in SP_BOOKS)

    for v in VERSIONS:
        if v == "SP":
            denom = sp_total
            n     = sp_null
        else:
            denom = total_tokens
            n     = nulls[v]
        fill = denom - n
        pct  = 100 * fill / denom if denom else 0
        if md:
            w(f"| {v} | {fill:,} | {n:,} | {pct:.1f}% |")
        else:
            w(f"  {v:<8}  {fill:>10,}  {n:>8,}  {pct:>6.1f}%")

    # Per-book fill rates
    if md:
        w("\n### Per-book fill rates\n")
        w("| Book | Tokens | LXX | VUL | SP | KJV | DSS |")
        w("|------|-------:|----:|----:|:--:|----:|----:|")
    else:
        w(f"\n  Per-book fill rates:")
        w(f"  {'Book':<22}  {'Tokens':>7}  {'LXX':>6}  {'VUL':>6}  {'SP':>6}  {'KJV':>6}  {'DSS':>6}")

    for book in sorted(book_totals):
        bt = book_totals[book]
        cells = []
        for v in VERSIONS:
            if v == "SP" and book not in SP_BOOKS:
                cells.append("N/A")
            else:
                n = book_nulls[book][v]
                cells.append(fmt_pct(100 * (bt - n) / bt) if bt else "—")
        if md:
            w(f"| {book} | {bt:,} | {' | '.join(cells)} |")
        else:
            w(f"  {book:<22}  {bt:>7,}  " + "  ".join(f"{c:>6}" for c in cells))

    output = "\n".join(lines)
    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        sys.stdout.buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    main()
