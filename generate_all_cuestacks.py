#!/usr/bin/env python3
"""
Generate one cue stack file for every CSV in assets/cue-stack-source.

Each CSV filename must follow:
    <number>_<name>.csv

The numeric prefix becomes the cue stack ID. The suffix becomes the cue stack
name. The script finds the matching audio file in assets/audio using a relaxed
name match that treats hyphens and underscores as equivalent.

Optionally, it can also write all generated cue stacks into one combined file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from generate_cuestack import generate_cue_stack, read_cues_from_csv


def normalize_name(value: str) -> str:
    return re.sub(r'[-_]+', '', value).lower()


def parse_stack_filename(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r'(\d+)_([^/]+)', path.stem)
    if not match:
        raise ValueError(
            f'CSV filename must match <number>_<name>.csv, got: {path.name}'
        )
    return int(match.group(1)), match.group(2)


def find_audio_file(audio_dir: Path, csv_path: Path) -> str:
    csv_key = normalize_name(csv_path.stem)
    matches = []
    for audio_path in audio_dir.iterdir():
        if not audio_path.is_file():
            continue
        if normalize_name(audio_path.stem) == csv_key:
            matches.append(audio_path.name)

    if not matches:
        raise FileNotFoundError(f'No matching audio file found for {csv_path.name}')
    if len(matches) > 1:
        raise ValueError(
            f'Multiple matching audio files found for {csv_path.name}: {matches}'
        )
    return matches[0]


def generate_all(
    csv_dir: Path,
    audio_dir: Path,
    output_dir: Path,
    combined_output: Path | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written_files: list[Path] = []
    combined_blocks: list[str] = []

    for csv_path in sorted(
        csv_dir.glob('*.csv'),
        key=lambda path: parse_stack_filename(path)[0],
    ):
        file_number, stack_name = parse_stack_filename(csv_path)
        stack_id = file_number + 1
        audio_file = find_audio_file(audio_dir, csv_path)
        cues = read_cues_from_csv(csv_path)
        if not cues:
            raise ValueError(f'No cues found in CSV: {csv_path.name}')

        block = generate_cue_stack(stack_id, stack_name, cues, audio_file)
        output_path = output_dir / f'{csv_path.stem}_cuestack.txt'
        output_path.write_text(block + '\n', encoding='utf-8')
        written_files.append(output_path)
        combined_blocks.append(block)

    if combined_output is not None:
        combined_output.parent.mkdir(parents=True, exist_ok=True)
        combined_output.write_text('\n\n'.join(combined_blocks) + '\n', encoding='utf-8')
        written_files.append(combined_output)

    return written_files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--csv-dir',
        default='assets/cue-stack-source',
        help='Directory containing cue stack CSV files',
    )
    parser.add_argument(
        '--audio-dir',
        default='assets/audio',
        help='Directory containing matching audio files',
    )
    parser.add_argument(
        '--output-dir',
        default='assets/cue-stacks',
        help='Directory where generated cue stack files are written',
    )
    parser.add_argument(
        '--combined-output',
        help='Optional file path for writing all generated cue stacks together',
    )
    args = parser.parse_args()

    try:
        written_files = generate_all(
            Path(args.csv_dir),
            Path(args.audio_dir),
            Path(args.output_dir),
            Path(args.combined_output) if args.combined_output else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))

    for output_path in written_files:
        print(f'Written {output_path}')


if __name__ == '__main__':
    main()