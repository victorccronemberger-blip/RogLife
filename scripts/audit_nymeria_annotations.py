"""Read-only annotation inventory and continuity diagnostics (no media/network)."""
import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    counts = Counter()
    samples = []
    components = []
    gap_samples = []
    joined_components = []
    garment_samples = []
    gap_counts = Counter()
    for seq in sorted(args.root.iterdir()):
        if not seq.is_dir() or seq.name.startswith('_'):
            continue
        path = seq / 'narration/atomic_action.csv'
        if not path.is_file():
            counts['missing_atomic_files'] += 1
            continue
        counts['atomic_files'] += 1
        valid, invalid = [], []
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                counts['rows'] += 1
                try:
                    start, end = float(row['start_time']), float(row['end_time'])
                    text = row['Describe my atomic actions'].strip()
                    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or not text:
                        raise ValueError()
                    valid.append((start, end, text))
                except (ValueError, KeyError, TypeError):
                    invalid.append(row)
        counts['valid_rows'] += len(valid)
        counts['invalid_rows'] += len(invalid)
        if invalid:
            counts['files_with_invalid_rows'] += 1
            counts['valid_rows_in_rejected_files'] += len(valid)
            if len(samples) < 6:
                samples.append({'sequence': seq.name, 'valid_rows': len(valid),
                                'invalid_rows': len(invalid), 'invalid_examples': invalid[:2]})
        else:
            merged = []
            joined = []
            for start, end, text in sorted(valid):
                if merged and start <= merged[-1][1] + 1e-6:
                    merged[-1][1] = max(end, merged[-1][1])
                else:
                    if merged:
                        gap = start - merged[-1][1]
                        gap_counts['at_most_100ms' if gap <= .1 else 'above_100ms'] += 1
                    if merged and len(gap_samples) < 12:
                        gap_samples.append({'sequence': seq.name, 'gap_seconds': start - merged[-1][1],
                                            'next_action': text})
                    merged.append([start, end])
                if joined and start <= joined[-1][1] + .1:
                    joined[-1][1] = max(end, joined[-1][1])
                else:
                    joined.append([start, end])
            components.extend(end - start for start, end in merged)
            joined_components.extend(end - start for start, end in joined)
            garment = [row for row in valid if 'fold' in row[2].lower()
                       and any(word in row[2].lower() for word in ('shirt', 'cloth', 'pant', 'laundry'))]
            if len(garment) >= 5 and len(garment_samples) < 6:
                garment_samples.append({'sequence': seq.name, 'folding_rows': len(garment),
                                        'examples': garment[:3]})
            counts['valid_recording_span_seconds'] += (max(row[1] for row in valid) - min(row[0] for row in valid)) if valid else 0
    report = {'inventory': dict(counts), 'annotation_components': len(components),
              'components_at_least_60_seconds': sum(value >= 60 for value in components),
              'components_at_least_180_seconds': sum(value >= 180 for value in components),
              'component_seconds': sum(components), 'invalid_samples': samples, 'gap_samples': gap_samples,
              'annotation_gap_counts': dict(gap_counts),
              'components_with_100ms_join': len(joined_components),
              'joined_components_at_least_60_seconds': sum(value >= 60 for value in joined_components),
              'garment_samples': garment_samples,
              'media_downloaded': False}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
