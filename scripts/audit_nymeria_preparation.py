"""Revalidate an acquired Nymeria plan and prepare real media without uploading."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plans", type=Path)
    parser.add_argument("--library-root", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["QMONEY_USER_ROOT"] = str(args.library_root.resolve())
    os.environ["QMONEY_LIBRARY_ROOT"] = str(args.library_root.resolve())
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    spec = importlib.util.spec_from_file_location("offline_audit_guard", project / "scripts/run_tests.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    exec(guard.OFFLINE_GUARD, {})
    from moneymin import campaign, nymeria
    plans = json.loads(args.plans.read_text(encoding="utf-8"))
    candidate = nymeria.resolve_planned_candidate(plans[0]["clip"])
    def progress(phase, payload):
        print(json.dumps({"phase": phase, **payload}), flush=True)
    item = campaign.prepare_nymeria_clip(
        candidate, args.work_dir, progress=progress, allow_download=False,
        should_stop=lambda: False)
    args.output.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: item[key] for key in (
        "clip_uid", "duration_ms", "video_path", "n_samples", "source", "probe")},
        ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
