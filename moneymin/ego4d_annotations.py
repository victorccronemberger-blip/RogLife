"""Official temporal annotations for on-demand Ego4D, without video pre-cache."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import threading
import uuid

from . import ego4d
from .atomic_io import save_json

_LOCK = threading.Lock()
ANNOTATIONS_KEY = "public/v2/annotations/narration.json"


def _object_records(stream):
    """Decode one top-level member at a time, bounding RAM to one recording."""
    decoder = json.JSONDecoder()
    buffer = ""
    ended = False

    def take():
        nonlocal buffer, ended
        chunk = stream.read(1024 * 1024)
        if not chunk:
            ended = True
        buffer += chunk

    def whitespace():
        nonlocal buffer
        buffer = buffer.lstrip()
        while not buffer and not ended:
            take()
            buffer = buffer.lstrip()

    def token(expected):
        nonlocal buffer
        whitespace()
        if not buffer.startswith(expected):
            raise ValueError("Anotações Ego4D com estrutura inválida.")
        buffer = buffer[len(expected):]

    def value():
        nonlocal buffer
        whitespace()
        while True:
            try:
                result, offset = decoder.raw_decode(buffer)
                buffer = buffer[offset:]
                return result
            except json.JSONDecodeError:
                if ended:
                    raise ValueError("Anotações Ego4D incompletas.") from None
                take()

    token("{")
    whitespace()
    if buffer.startswith("}"):
        token("}")
    else:
        while True:
            uid = value()
            if not isinstance(uid, str) or not uid:
                raise ValueError("Identidade de anotação Ego4D inválida.")
            token(":")
            yield uid, value()
            whitespace()
            if buffer.startswith("}"):
                token("}")
                break
            token(",")
    whitespace()
    if buffer:
        raise ValueError("Anotações Ego4D com conteúdo adicional inválido.")


def convert_narrations(source: Path, destination: Path, eligible_uids: set[str]) -> dict:
    """Convert only real timestamped annotations for catalog parents with IMU."""
    videos = events_count = rejected = 0
    seen = set()
    with source.open(encoding="utf-8-sig") as stream, destination.open("w", encoding="utf-8") as out:
        for uid, record in _object_records(stream):
            if uid in seen or not isinstance(record, dict):
                raise ValueError("Registro de anotação Ego4D inválido ou duplicado.")
            seen.add(uid)
            if uid not in eligible_uids:
                continue
            events = []
            for name, annotations in record.items():
                if not name.startswith("narration_pass_"):
                    continue
                if not isinstance(annotations, dict) or not isinstance(annotations.get("narrations"), list):
                    raise ValueError("Passagem de anotação Ego4D inválida.")
                for annotation in annotations["narrations"]:
                    if not isinstance(annotation, dict):
                        raise ValueError("Evento Ego4D inválido.")
                    when, text = annotation.get("timestamp_sec"), annotation.get("narration_text")
                    if (type(when) not in (int, float) or not math.isfinite(when) or when < 0
                            or not isinstance(text, str) or not text.strip()):
                        rejected += 1
                        continue
                    if "#summary" in text.casefold().replace(" ", ""):
                        continue
                    events.append((float(when), text.strip()))
            if events:
                events = sorted(set(events))
                out.write(json.dumps({"video_uid": uid, "events": events}, ensure_ascii=False) + "\n")
                videos += 1
                events_count += len(events)
        out.flush()
        os.fsync(out.fileno())
    if not videos or not events_count:
        raise ValueError("Nenhuma anotação temporal Ego4D válida foi recebida.")
    return {"videos": videos, "events": events_count, "rejected_events": rejected}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_timed_annotations(*, force=False, progress=None) -> dict:
    with _LOCK:
        meta, _clips = ego4d.sync_meta()
        target = ego4d.timed_narrations_path()
        receipt = target.with_name("timed_narrations.source.json")
        if not force and target.is_file() and receipt.is_file():
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            if (saved.get("schema") == 1 and saved.get("source_key") == ANNOTATIONS_KEY
                    and saved.get("output_sha256") == _sha256(target)
                    and saved.get("catalog_sha256") == _sha256(meta)):
                return saved
        target.parent.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex
        source = target.with_name(f".narration-{tag}.json")
        staging = target.with_name(f".timed-narrations-{tag}.jsonl")
        try:
            if progress:
                progress("Baixando anotações temporais oficiais Ego4D; nenhum vídeo é preparado…")
            ego4d._download_to(ego4d.MANIFEST_BUCKET, ANNOTATIONS_KEY, source)
            if progress:
                progress("Indexando ações e timestamps oficiais Ego4D…")
            metadata = json.loads(meta.read_text(encoding="utf-8"))
            eligible = {v["video_uid"] for v in metadata["videos"] if v.get("has_imu") is True}
            stats = convert_narrations(source, staging, eligible)
            provenance = {"schema": 1, "source_bucket": ego4d.MANIFEST_BUCKET,
                "source_key": ANNOTATIONS_KEY, "source_bytes": source.stat().st_size,
                "source_sha256": _sha256(source), "output_sha256": _sha256(staging),
                "catalog_sha256": _sha256(meta), **stats}
            staging.replace(target)
            save_json(receipt, provenance)
            ego4d._timed_bytes_cached.cache_clear()
            return provenance
        finally:
            source.unlink(missing_ok=True)
            staging.unlink(missing_ok=True)
