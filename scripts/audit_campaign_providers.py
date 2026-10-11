"""Audit campaign candidate capacity without downloading or submitting media.

Supply a sanitized task snapshot, never account credentials. Persistent ranking
files are isolated in a temporary user root; the source library is read only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library_root", type=Path)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--provider", choices=("ego4d", "nymeria"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tasks = json.loads(args.tasks.read_text(encoding="utf8"))
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    from run_tests import OFFLINE_GUARD
    exec(OFFLINE_GUARD, {})
    with tempfile.TemporaryDirectory(prefix="qmoney-provider-audit-") as temporary:
        os.environ["QMONEY_USER_ROOT"] = temporary
        os.environ["QMONEY_LIBRARY_ROOT"] = str(args.library_root.resolve())
        from moneymin import campaign, ego4d, task_matching
        started = time.monotonic()
        rows = []
        with ego4d.selection_operation():
            for task in tasks:
                name = task["name"]
                raw = campaign._compatible_task_clips(name, args.provider, catalog_only=True)
                accepted = [clip for clip in raw if campaign._catalog_queue_accepts(clip, name)]
                audio = [clip for clip in accepted if not task.get("requiresAudio")
                         or campaign._candidate_audio_possible(clip)]
                row = {"task": name, "mapping_supported": task_matching.rule_for(name) is not None,
                       "raw_candidates": len(raw), "evidence_accepted": len(accepted),
                       "audio_accepted": len(audio),
                       "annotation_seconds_upper_bound": sum(clip["dur_s"] for clip in audio),
                       "readiness": dict(Counter(clip.get("readiness", "source_metadata") for clip in audio))}
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        report = {"provider": args.provider, "tasks": rows,
                  "candidate_count": sum(row["audio_accepted"] for row in rows),
                  "tasks_with_candidates": sum(row["audio_accepted"] > 0 for row in rows),
                  "annotation_hours_upper_bound": sum(row["annotation_seconds_upper_bound"] for row in rows) / 3600,
                  "elapsed_seconds": round(time.monotonic() - started, 2),
                  "external_network_blocked": True, "media_downloaded": False,
                  "note": "Sum before footage deduplication; availability and measured sensors are checked during preparation."}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({key: value for key, value in report.items() if key != "tasks"}), flush=True)


if __name__ == "__main__":
    main()
