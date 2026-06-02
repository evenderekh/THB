"""
wlc_thought_inventory.py
Inventory completeness of thb/backend/alignment/thought/wlc/ against thb.mt.json.

Reports:
  1. Chapter-level completeness per book
  2. Verse-level completeness per book (summary + detail)
  3. Thought translation coverage per version, context-aware for SP
  4. english_reference fill rate
"""

import json
import os
import re
from collections import defaultdict

MT_JSON  = os.path.join(os.path.dirname(__file__), "..", "..", "thb.mt.json")
WLC_DIR  = os.path.join(os.path.dirname(__file__), "wlc")
VERSIONS = ["WLC", "LXX", "VUL", "SP", "KJV", "DSS"]

SP_BOOKS = {"Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy"}


def load_mt_index(path):
    with open(path, encoding="utf-8") as f:
        mt = json.load(f)
    index = {}
    for book, chapters in mt.items():
        index[book] = {}
        for ch_obj in chapters:
            ch = ch_obj["chapter"]
            index[book][ch] = {v_obj["verse"] for v_obj in ch_obj.get("verses", [])}
    return index


BOOK_ALIASES = {
    "Song of Solomon": "Song of Songs",
}

def parse_filename(name):
    m = re.match(r"^(.+)\.(\d+)\.json$", name)
    if m:
        book = m.group(1).replace("_", " ")
        return BOOK_ALIASES.get(book, book), int(m.group(2))
    return None


def load_wlc_files(wlc_dir):
    """Return {book: {chapter: verse_set}}."""
    result = defaultdict(dict)
    for fname in os.listdir(wlc_dir):
        parsed = parse_filename(fname)
        if not parsed:
            continue
        book, ch = parsed
        with open(os.path.join(wlc_dir, fname), encoding="utf-8") as f:
            data = json.load(f)
        result[book][ch] = {int(v) for v in data.keys()}
    return result


def count_thoughts_and_nulls(wlc_dir):
    """Return (total, {version: null_count}, eng_null, book_totals, book_nulls, book_eng_null)."""
    total     = 0
    nulls     = defaultdict(int)
    eng_null  = 0
    book_totals   = defaultdict(int)
    book_nulls    = defaultdict(lambda: defaultdict(int))
    book_eng_null = defaultdict(int)

    for fname in sorted(os.listdir(wlc_dir)):
        parsed = parse_filename(fname)
        if not parsed:
            continue
        book, _ = parsed
        with open(os.path.join(wlc_dir, fname), encoding="utf-8") as f:
            data = json.load(f)
        for verse_thoughts in data.values():
            for thought in verse_thoughts:
                total += 1
                book_totals[book] += 1
                er = thought.get("english_reference")
                if er is None or (isinstance(er, str) and er.strip() == ""):
                    eng_null += 1
                    book_eng_null[book] += 1
                trad = thought.get("traditions", {})
                for v in VERSIONS:
                    val = trad.get(v)
                    if val is None or (isinstance(val, str) and val.strip() == ""):
                        nulls[v] += 1
                        book_nulls[book][v] += 1

    return total, nulls, eng_null, book_totals, book_nulls, book_eng_null


def fmt_pct(p):
    return f"{p:.0f}%"


def main():
    import sys
    md = "--md" in sys.argv
    out_file = next((a for a in sys.argv[1:] if not a.startswith("--")), None)

    print("Loading thb.mt.json …",    file=sys.stderr)
    mt = load_mt_index(MT_JSON)
    print("Scanning thought/wlc …",   file=sys.stderr)
    wlc = load_wlc_files(WLC_DIR)
    print("Counting thoughts …",      file=sys.stderr)
    total, nulls, eng_null, book_totals, book_nulls, book_eng_null = count_thoughts_and_nulls(WLC_DIR)

    lines = []
    w = lines.append

    if md:
        w("# WLC Thought Alignment Inventory\n")
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
    book_verse_exp  = defaultdict(int)
    book_verse_pres = defaultdict(int)
    gap_rows = []

    for book in sorted(mt):
        for ch in sorted(mt[book]):
            exp_vs  = mt[book][ch]
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
        w("\n### Detail — missing verses per chapter\n")
        w("| Book | Ch | Missing verses |")
        w("|------|----|----------------|")
        for book, ch, miss_vs in gap_rows:
            w(f"| {book} | {ch} | {', '.join(str(v) for v in miss_vs)} |")
    else:
        w(f"\n  {'TOTAL':<22}  {total_pres:>10,}  {total_exp:>7,}  {overall:>5.1f}%")
        w("\n  Detail — missing verses per chapter:")
        for book, ch, miss_vs in gap_rows:
            w(f"  {book} {ch}: {miss_vs}")

    # ── 3. Thought translation coverage ──────────────────────────────────────
    sp_total = sum(book_totals[b] for b in book_totals if b in SP_BOOKS)
    sp_null  = sum(book_nulls[b]["SP"] for b in book_nulls if b in SP_BOOKS)

    if md:
        w("\n## 3. Thought Translation Coverage\n")
        w(f"**{total:,}** thoughts across all present files.\n")
        w("> SP fill % is computed only over Pentateuch books.\n")
        w("| Field | Filled | Null | Fill % |")
        w("|-------|-------:|-----:|-------:|")
    else:
        w("\n" + "=" * 70)
        w("3. THOUGHT TRANSLATION COVERAGE")
        w("=" * 70)
        w(f"\n  Total thoughts: {total:,}")
        w("  (SP fill % scoped to Pentateuch only)\n")
        w(f"  {'Field':<18}  {'Filled':>10}  {'Null':>8}  {'Fill %':>7}")
        w(f"  {'-'*18}  {'-'*10}  {'-'*8}  {'-'*7}")

    # english_reference row first
    eng_fill = total - eng_null
    eng_pct  = 100 * eng_fill / total if total else 0
    if md:
        w(f"| english_reference | {eng_fill:,} | {eng_null:,} | {eng_pct:.1f}% |")
    else:
        w(f"  {'english_reference':<18}  {eng_fill:>10,}  {eng_null:>8,}  {eng_pct:>6.1f}%")

    for v in VERSIONS:
        if v == "SP":
            denom = sp_total
            n     = sp_null
        else:
            denom = total
            n     = nulls[v]
        fill = denom - n
        pct  = 100 * fill / denom if denom else 0
        if md:
            w(f"| {v} | {fill:,} | {n:,} | {pct:.1f}% |")
        else:
            w(f"  {v:<18}  {fill:>10,}  {n:>8,}  {pct:>6.1f}%")

    # Per-book fill rates
    if md:
        w("\n### Per-book fill rates\n")
        w("| Book | Thoughts | eng_ref | WLC | LXX | VUL | SP | KJV | DSS |")
        w("|------|---------:|--------:|----:|----:|----:|:--:|----:|----:|")
    else:
        w(f"\n  Per-book fill rates:")
        w(f"  {'Book':<22}  {'Thoughts':>8}  {'eng_ref':>7}" + "".join(f"  {v:>6}" for v in VERSIONS))

    for book in sorted(book_totals):
        bt = book_totals[book]
        en = book_eng_null[book]
        eng_p = fmt_pct(100 * (bt - en) / bt) if bt else "—"
        cells = []
        for v in VERSIONS:
            if v == "SP" and book not in SP_BOOKS:
                cells.append("N/A")
            else:
                n = book_nulls[book][v]
                cells.append(fmt_pct(100 * (bt - n) / bt) if bt else "—")
        if md:
            w(f"| {book} | {bt:,} | {eng_p} | {' | '.join(cells)} |")
        else:
            w(f"  {book:<22}  {bt:>8,}  {eng_p:>7}" + "".join(f"  {c:>6}" for c in cells))

    output = "\n".join(lines)
    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        sys.stdout.buffer.write(output.encode("utf-8"))


if __name__ == "__main__":
    main()
