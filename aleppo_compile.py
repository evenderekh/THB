"""
Compile ./aleppo/json/*.json into thb/backend/thb.1.3.aleppo.json.

Each source file describes one facsimile page. The output is a list sorted
by file number (= facsimile page order), ready for linear range-scan lookup
by the THB builder.
"""
import json
import glob
import os
from pathlib import Path

SOURCE_GLOB = str(Path(__file__).parent.parent / 'aleppo' / 'json' / '*.json')
OUTPUT_PATH = Path(__file__).parent / 'backend' / 'thb.1.3.aleppo.json'


def main():
    entries = []
    for path in sorted(glob.glob(SOURCE_GLOB)):
        file_num = int(os.path.splitext(os.path.basename(path))[0])
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        entries.append({
            'file':         file_num,
            'book':         d['book'],
            'ch_from':      d['opening_chapter'],
            'v_from':       d['opening_verse'],
            'closing_book': d['closing_book'],
            'ch_to':        d['closing_chapter'],
            'v_to':         d['closing_verse'],
        })

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"Written {len(entries)} entries to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
