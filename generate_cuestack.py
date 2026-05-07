#!/usr/bin/env python3
"""
Generate a MagicQ .shw cue stack (C record) from a CSV file.

CSV format:
    time,comment
    0:00,"first cue"
    00:04.0,"second cue"

Times are in M:SS or MM:SS.d (decimal = tenths of a second).
Cues alternate between scene 0x0029 (red) and 0x002a (blue).

Usage:
    # Print generated block to stdout
    python generate_cuestack.py blok1.csv

    # Write to a file
    python generate_cuestack.py blok1.csv -o blok1_cuestack.txt

    # Patch an existing .shw file, replacing the C,0002 stack in place
    python generate_cuestack.py blok1.csv --patch "ROM SHOW 2026 - 20260503_1732.shw"

    # Different stack ID, name, or audio file
    python generate_cuestack.py blok1.csv --stack-id 0003 --stack-name "blok 2" \
        --audio-file "1_blok1.mp3"
"""

import argparse
import csv
import re
import sys
from pathlib import Path

# ── Scene IDs to alternate between ──────────────────────────────────────────
SCENE_RED  = "0029"
SCENE_BLUE = "002a"

# ── Stack-level options line (copied from working stack in the show file) ───
STACK_OPTIONS = (
    "00000709,003c,003c,003c,003c,003c,003c,"
    "0000,0000,0000,0000,0000,0100,0000,0001,0001,00000000,"
)

# ── Per-cue flags (field 5 on each cue row) ─────────────────────────────────
CUE_FLAGS_FIRST = "00005200"
CUE_FLAGS = "00015200"


# ── Time parsing ─────────────────────────────────────────────────────────────

def parse_time_cs(s: str) -> int:
    """
    Parse a time string to centiseconds.

    Accepted formats:
        M:SS       e.g. "0:00"   → 0
        H:MM:SS    e.g. "0:00:16" → 1600
        MM:SS      e.g. "01:26"  → 8600
        MM:SS.d    e.g. "00:04.0" → 400
        MM:SS.dd   e.g. "05:12.77" → 31277
    """
    s = s.strip()
    m = re.fullmatch(r'(?:(\d+):)?(\d+):(\d{2})(?:\.(\d{1,2}))?', s)
    if not m:
        raise ValueError(f"Unrecognised time format: {s!r}")
    hours = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    fraction = m.group(4) or ''
    if len(fraction) == 1:
        centiseconds = int(fraction) * 10
    elif len(fraction) == 2:
        centiseconds = int(fraction)
    else:
        centiseconds = 0
    return (hours * 3600 + minutes * 60 + seconds) * 100 + centiseconds


# ── Record generator ─────────────────────────────────────────────────────────

def read_cues_from_csv(csv_file: Path) -> list[tuple[int, str]]:
    """
    Read cue timings and comments from a CSV file.
    """
    cues: list[tuple[int, str]] = []
    with csv_file.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            time_cs = parse_time_cs(row['time'])
            comment = row['comment'].strip().strip('"')
            cues.append((time_cs, comment))
    return cues

def generate_cue_stack(stack_id: int, stack_name: str,
                       cues: list[tuple[int, str]],
                       audio_file: str = "") -> str:
    """
    Build the full C record text for a cue stack.

    cues: list of (time_centiseconds, label) tuples, ordered by time.
    """
    n   = len(cues)
    sid = f"{stack_id:04x}"
    scenes = [SCENE_RED, SCENE_BLUE]

    out = []

    # ── Header ────────────────────────────────────────────────────────────
    out.append(f'C,{sid},"{stack_name}",0000,{n:04x},0000801e,')
    out.append(STACK_OPTIONS)

    # ── Cue rows ─────────────────────────────────────────────────────────
    for i, (_, label) in enumerate(cues):
        next_i = (i + 1) % n
        prev_i = (i - 1) % n
        scene  = scenes[i % 2]
        cue_flags = CUE_FLAGS_FIRST if i == 0 else CUE_FLAGS
        out.append(
            f'{next_i:04x},{prev_i:04x},{scene},0000,{cue_flags},'
            f'{i + 1:.6f},"{label}",'
        )

    # ── Comment block ─────────────────────────────────────────────────────
    # First entry carries the leading flag byte (00000001); rest do not.
    _, c0 = cues[0]
    out.append(f'00000001,"","{c0}",')
    for _, comment in cues[1:]:
        out.append(f'"","{comment}",')
    out.append('')  # blank line separator

    # ── Static separator ──────────────────────────────────────────────────
    out.append('00000000,0000,0000,0000,0000,')

    # ── Timecodes: one line per cue (cue 1 is always 00000000) ───────────
    for time_cs, _ in cues:
        out.append(f'{time_cs:08x},')

    # ── Remaining stack metadata (cloned from known-good stack) ───────────
    out.append('0000,00000000,0100,0100,00000000,')
    out.append('00000014,00000f00,0000,0000,')
    out.append('0000,0000,000a,')
    out.append('0000,0000,0000,0000,0000,0000,0000,0000,0000,0000,')
    out.append(f'00000000,00000000,0,"{audio_file}",')
    out.append('00000000,00000000,00000000,00000000,')
    out.append(
        '00000000,00000000,00000000,"",0000,0000,0000,0000,'
        '0000000e,00000000000D965C,00000000,00000000,;'
    )

    return '\n'.join(out)


# ── Patch helper ──────────────────────────────────────────────────────────────

def patch_show_file(path: Path, stack_id: int, new_block: str) -> None:
    """
    Replace the C,XXXX record matching stack_id inside a .shw file.
    The record runs from the C, line up to and including the ,; terminator.
    """
    # .shw files are Latin-1 encoded
    text = path.read_text(encoding='latin-1')
    sid  = f'{stack_id:04x}'

    # Locate the start of this C record
    start_m = re.search(rf'^C,{sid},', text, re.MULTILINE)
    if not start_m:
        raise ValueError(f'Stack C,{sid} not found in {path}')
    start = start_m.start()

    # Locate the end: first ,;\n after start
    end_m = re.search(r',;\n', text[start:])
    if not end_m:
        raise ValueError('Could not find end-of-record marker (,;) for stack')
    end = start + end_m.end()

    old_len = end - start
    patched = text[:start] + new_block + '\n' + text[end:]
    path.write_text(patched, encoding='latin-1')
    print(f'Patched {path}')
    print(f'  Replaced C,{sid}: {old_len} chars → {len(new_block) + 1} chars')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('csv_file',
                    help='CSV file with columns: time, comment')
    ap.add_argument('--stack-id', default='0002',
                    help='Cue stack ID in hex (default: 0002)')
    ap.add_argument('--stack-name', default='blok 1',
                    help='Name of the cue stack (default: "blok 1")')
    ap.add_argument('--audio-file', default='',
                    help='Optional audio filename for the stack metadata')
    ap.add_argument('--patch', metavar='SHOW_FILE',
                    help='Patch this .shw file, replacing the matching C,XXXX block')
    ap.add_argument('--output', '-o', metavar='FILE',
                    help='Write output to file instead of stdout')
    args = ap.parse_args()

    try:
        stack_id = int(args.stack_id, 16)
    except ValueError:
        ap.error(f'--stack-id must be a hex value, got: {args.stack_id!r}')

    cues = read_cues_from_csv(Path(args.csv_file))

    if not cues:
        sys.exit('No cues found in CSV.')

    block = generate_cue_stack(stack_id, args.stack_name, cues, args.audio_file)

    if args.patch:
        patch_show_file(Path(args.patch), stack_id, block)
    elif args.output:
        Path(args.output).write_text(block + '\n', encoding='utf-8')
        print(f'Written to {args.output}')
    else:
        print(block)


if __name__ == '__main__':
    main()
