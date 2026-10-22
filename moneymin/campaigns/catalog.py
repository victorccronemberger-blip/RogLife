"""Ranking, candidate pools and catalog assembly for campaigns."""
from __future__ import annotations
from ..background_work import checkpoint, report_progress
from ..catalog_policy import (
    CatalogPreparationRequired,
    persist_index_requested,
    prepared_catalog_read,
    prepared_only_requested,
    require_prepared_index,
)

def _task_candidates(_services) -> tuple[dict[str, Any], ...]:
    """Catálogo Ego4D+IMU (clipes oficiais). A junção por task vem depois."""
    return tuple(_services.ego4d.list_clips(
        scenario=None, min_dur_s=60, max_dur_s=1800,
        require_imu=True, max_results=None))

def _rank_cache_path(_services) -> Path:
    return _services.config.DATA_DIR / "task_rank_cache.pkl"

def _rank_seed_path(_services) -> Path:
    """Índice portátil mínimo distribuído com o motor local."""
    return _services.Path(__file__).resolve().parent.parent / "resources" / "ego4d_task_rank_seed.json.gz"

def _rank_seed_bytes_cached(_services, raw: bytes | None) -> dict[str, tuple[dict[str, Any], ...]] | None:
    """Carrega IDs e janelas; o arquivo não contém mídia, segredo ou narração."""
    try:
        payload = _services.json.loads(_services.gzip.decompress(raw)) if raw is not None else {}
    except (OSError, ValueError, TypeError, _services.gzip.BadGzipFile):
        return None
    if payload.get("schema") != 1 or not isinstance(payload.get("tasks"), dict):
        return None
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for name, items in payload["tasks"].items():
        if not isinstance(name, str) or not isinstance(items, list):
            return None
        valid: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                return None
            if (str(item.get("parent_video_uid") or "")
                    in _services.ego4d.KNOWN_INCOMPLETE_IMU_VIDEO_UIDS):
                continue
            try:
                duration = float(item.get("dur_s") or 0)
            except (TypeError, ValueError):
                return None
            if (not item.get("clip_uid") or not item.get("parent_video_uid")
                    or not str(item.get("s3_path") or "").startswith("s3://")
                    or duration < 60):
                return None
            # Older portable seeds stored a rounded display duration. They are
            # suggestions, not permission to rewrite/re-certify stale records.
            # Skip inconsistent entries before deduplication and evidence
            # binding, rather than letting one obsolete row abort the catalog.
            window = _services._clip_window(item)
            if (window is None or not _services.math.isfinite(duration)
                    or abs(duration - (window[1] - window[0]))
                    > _services.task_matching.TEMPORAL_EPSILON_S):
                continue
            valid.append(dict(item))
        result[name] = tuple(valid)
    return result

def _load_rank_seed(_services) -> dict[str, tuple[dict[str, Any], ...]] | None:
    return _services._rank_seed_bytes_cached(_services.ego4d._selection_source(_services._rank_seed_path())[0])

def _rank_cache_stamp(_services) -> tuple[tuple[str, int, str], ...]:
    """Content of the exact bound parser inputs; once per operation boundary."""
    _services.ego4d._selection_rank_seed(_services._rank_seed_path())
    files = (
        _services.config.MEDIA_DATA_DIR / "ego4d" / "ego4d.json",
        _services.config.MEDIA_DATA_DIR / "ego4d" / "clips.csv",
        _services.config.MEDIA_DATA_DIR / "ego4d" / "clip_narrations.json",
        _services.config.MEDIA_DATA_DIR / "ego4d" / "timed_narrations.jsonl",
        _services.Path(_services.ego4d.__file__),
        _services.Path(_services.task_matching.__file__),
        _services._rank_seed_path(),
    )
    stamp: list[tuple[str, int, str]] = []
    for path in files:
        # Caminhos absolutos e mtimes mudam ao extrair em outro computador.
        try:
            relative = path.resolve().relative_to(_services.config.ROOT.resolve()).as_posix()
        except ValueError:
            # Testes, instalações portáteis e DATA_DIR externo podem ficar fora
            # do checkout; o nome lógico ainda produz uma assinatura estável.
            relative = path.name
        source = _services.ego4d._selection_source(path)
        stamp.append((relative, source[1], source[2]))
    # Frozen modules live in PYZ; their __file__ paths need not exist. Bind the
    # actual rules and aliases as well, so an installed update cannot reuse an
    # index built with an older catalogue merely because both sources are missing.
    rules = _services.ego4d._selection_rules_binding()
    stamp.append(("task-rules-semantic", rules["bytes"], rules["sha256"]))
    # Logic changes outside the declared rules also invalidate duration caches.
    stamp.append(("ranked-union", 12, _services.ego4d._SELECTION_VERSION))
    return tuple(stamp)

def _rank_stamp_clear(_services) -> None:
    # The stamp itself is no longer memoized without its content-bound key.
    pass

def _rank_evidenced(_services, buckets):
    result = {}
    total = sum(len(clips) for clips in buckets.values())
    completed = 0
    for name, clips in buckets.items():
        selected = []
        for clip in clips:
            checkpoint()
            if completed % 25 == 0 and total:
                report_progress(f"Vinculando evidências Ego4D: {completed}/{total} trechos",
                                phase="ego4d_bindings", completed=completed, total=total)
            selected.append(_services.ego4d.attach_selection_evidence(clip, name))
            completed += 1
        result[name] = tuple(selected)
    return result

def _clip_window(_services, clip: dict[str, Any]) -> tuple[float, float] | None:
    if (clip.get("source") == "nymeria"
            and clip.get("source_clock_domain") == "aria_DEVICE_TIME_ns"):
        canonical = clip.get("device_window_ns") or clip.get("planned_device_window_ns")
        if (isinstance(canonical, (list, tuple)) and len(canonical) == 2
                and all(type(value) is int and value >= 0 for value in canonical)
                and canonical[1] > canonical[0]):
            return canonical[0] / 1e9, canonical[1] / 1e9
        return None
    window = clip.get("window_s")
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        return None
    try:
        start, end = float(window[0]), float(window[1])
    except (TypeError, ValueError):
        return None
    if not _services.math.isfinite(start) or not _services.math.isfinite(end) or start < 0 or end <= start:
        return None
    return start, end

def _same_action_already_covered(_services, 
    kept: list[dict[str, Any]], clip: dict[str, Any],
) -> bool:
    """O corte oficial não entra de novo quando um trecho narrado já cobre a ação."""
    parent = str(clip.get("parent_video_uid") or "")
    window = _services._clip_window(clip)
    if not parent or window is None:
        return False
    start, end = window
    span = end - start
    for other in kept:
        if str(other.get("parent_video_uid") or "") != parent:
            continue
        other_window = _services._clip_window(other)
        if other_window is None:
            continue
        overlap = min(end, other_window[1]) - max(start, other_window[0])
        if overlap >= min(span, other_window[1] - other_window[0]) * 0.6:
            return True
    return False

def _union_ranked_clips(_services, 
    spans: dict[str, list[dict[str, Any]] | tuple[dict[str, Any], ...]],
    official: dict[str, list[dict[str, Any]] | tuple[dict[str, Any], ...]],
    *,
    min_dur_s: float = 60,
    max_dur_s: float = 1800,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Junta cortes narrados e clipes oficiais que passam na mesma prova de ação.

    Os dois lados já foram aceitos por `score_action`. O cenário sozinho não
    entra. O trecho narrado permanece primeiro.
    """
    names = set(spans) | set(official)
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for name in names:
        kept: list[dict[str, Any]] = []
        seen: set[str] = set()
        by_parent: dict[str, list[dict[str, Any]]] = {}

        def add(clip: dict[str, Any], *, allow_overlap: bool) -> None:
            try:
                duration = float(clip.get("dur_s") or 0)
            except (TypeError, ValueError):
                return
            if not min_dur_s <= duration <= max_dur_s:
                return
            uid = str(clip.get("clip_uid") or "")
            if not uid or uid in seen:
                return
            parent = str(clip.get("parent_video_uid") or "")
            overlapping = [other for other in by_parent.get(parent, ())
                           if _services._same_action_already_covered([other], clip)]
            aliases = set(clip.get("dedup_clip_uids") or ())
            for other in overlapping:
                # Alternate catalog IDs must not turn previously sent material
                # into fresh content when the catalog is expanded.
                aliases.add(str(other["clip_uid"]))
                aliases.update(other.get("dedup_clip_uids") or ())
                other["dedup_clip_uids"] = sorted(
                    (set(other.get("dedup_clip_uids") or ())
                     | set(clip.get("dedup_clip_uids") or ()) | {uid})
                    - {str(other["clip_uid"])})
            if not allow_overlap and overlapping:
                # A short narrated core must not erase a much longer verified
                # candidate. Only discard when the candidate itself is mostly
                # covered; aliases above still prevent resending its old core.
                window = _services._clip_window(clip)
                covered = []
                if window is not None:
                    for other in by_parent.get(parent, ()):
                        other_window = _services._clip_window(other)
                        if other_window is not None:
                            a = max(window[0], other_window[0])
                            b = min(window[1], other_window[1])
                            if b > a:
                                covered.append((a, b))
                    merged_end = window[0]
                    seconds = 0.0
                    for a, b in sorted(covered):
                        seconds += max(0.0, b - max(a, merged_end))
                        merged_end = max(merged_end, b)
                    if seconds < (window[1] - window[0]) * 0.6:
                        overlapping = []
                if overlapping:
                    return
            seen.add(uid)
            item = dict(clip)
            if aliases:
                item["dedup_clip_uids"] = sorted(aliases - {uid})
            kept.append(item)
            if parent:
                by_parent.setdefault(parent, []).append(item)

        for clip in spans.get(name, ()):
            add(clip, allow_overlap=True)
        def number(value: Any) -> float:
            try:
                value = float(value or 0)
                return value if _services.math.isfinite(value) else 0
            except (TypeError, ValueError):
                return 0

        extras = list(official.get(name, ()))
        extras.sort(key=lambda clip: (
            -number(clip.get("match_score")),
            -number(clip.get("dur_s")),
            str(clip.get("clip_uid") or ""),
        ))
        for clip in extras:
            add(clip, allow_overlap=False)
        result[name] = tuple(kept)
    return result

def _link_rank_history(_services, 
    current: dict[str, tuple[dict[str, Any], ...]],
    previous: dict[str, tuple[dict[str, Any], ...]],
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Old IDs are provenance, not permission to retain rejected old content."""
    old_by_task: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for name, clips in previous.items():
        task = _services.task_matching.canonical_task_name(name)
        parents = old_by_task.setdefault(task, {})
        for clip in clips:
            parent = str(clip.get("parent_video_uid") or "")
            parents.setdefault(parent, {})[str(clip.get("clip_uid") or "")] = clip
    linked = {}
    for name, clips in current.items():
        parents = old_by_task.get(_services.task_matching.canonical_task_name(name), {})
        result = []
        for candidate in clips:
            clip = dict(candidate)
            uid = str(clip.get("clip_uid") or "")
            aliases = set(clip.get("dedup_clip_uids") or ())
            for old_uid, old in parents.get(str(clip.get("parent_video_uid") or ""), {}).items():
                if uid == old_uid or _services._same_action_already_covered([old], clip):
                    aliases.add(old_uid)
                    aliases.update(old.get("dedup_clip_uids") or ())
            aliases.discard(uid)
            if aliases:
                clip["dedup_clip_uids"] = sorted(aliases)
            result.append(clip)
        linked[name] = tuple(result)
    return linked

def _narration_file_present(_services, path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False

def _local_narration_catalog(_services) -> bool:
    """A biblioteca ativa tem narração. O índice portátil não a substitui."""
    return _services.ego4d.has_timed_narrations() or _services._narration_file_present(
        _services.config.MEDIA_DATA_DIR / "ego4d" / "clip_narrations.json")

def _stamp_hides_present_narration(_services, stamp) -> bool:
    """Cache gravado com narração ausente não vale depois que o arquivo existe."""
    present = {
        "clip_narrations.json": _services._narration_file_present(
            _services.config.MEDIA_DATA_DIR / "ego4d" / "clip_narrations.json"),
        "timed_narrations.jsonl": _services._narration_file_present(_services.ego4d.timed_narrations_path()),
    }
    for item in stamp:
        if not item:
            continue
        name = str(item[0]).replace("\\", "/").rsplit("/", 1)[-1]
        if not present.get(name):
            continue
        size = item[1] if len(item) > 1 else 0
        digest = str(item[2]) if len(item) > 2 else ""
        if size == 0 or digest == "missing":
            return True
    return False

def _prepare_queue_accepts(_services, clip: dict[str, Any], task_name: str | None, *,
                           fresh: bool = True) -> bool:
    """Trecho sem prova na biblioteca atual não entra na fila de preparo."""
    source = str(clip.get("source") or "")
    uid = str(clip.get("clip_uid") or "")
    if source == "nymeria" or uid.startswith("nymeria:"):
        try:
            if clip.get("acquisition_required") is True:
                _services.nymeria.revalidate_planned_candidate(clip, task_name=task_name)
            else:
                _services.nymeria.revalidate_candidate(clip, task_name=task_name, fresh=fresh)
        except (OSError, ValueError, RuntimeError, ImportError):
            return False
        return True
    if source == "holoassist":
        return True
    # The portable seed can suggest a source, but queue admission must match
    # the same current evidence gate used by preparation on every installation.
    # Legacy/prepared rows without a carrier are not current task proof either.
    try:
        _services.ego4d.revalidate_selection_evidence(clip, task_name=task_name, fresh=fresh)
    except ValueError:
        return False
    return True

def _merge_rank_seed(_services, 
    buckets: dict[str, tuple[dict[str, Any], ...]],
    *,
    min_dur_s: float = 60,
    max_dur_s: float = 1800,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Completa qualquer cache local com o índice portátil do executável.

    Versões antigas podiam gravar um cache válido, porém vazio, antes de o
    catálogo Ego4D terminar de ser preparado. Como o cache persistia entre
    atualizações, uma instalação nova continuava mostrando todas as categorias
    desabilitadas mesmo depois de receber o índice portátil. O índice embutido
    agora é sempre a base mínima; dados locais completos apenas o enriquecem.
    """
    seed = _services._load_rank_seed() or {}
    # Keep the portable seed's alternatives available (different duration
    # requests can need them), while linking their overlapping identities.
    combined = _services._union_ranked_clips(
        {name: [*buckets.get(name, ()), *seed.get(name, ())]
         for name in buckets.keys() | seed.keys() | _services.task_matching.TASK_RULES.keys()}, {},
        min_dur_s=min_dur_s, max_dur_s=max_dur_s)
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for name, candidates in combined.items():
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            if (str(item.get("parent_video_uid") or "")
                    in _services.ego4d.KNOWN_INCOMPLETE_IMU_VIDEO_UIDS):
                continue
            try:
                duration = float(item.get("dur_s") or 0)
            except (TypeError, ValueError):
                continue
            if not min_dur_s <= duration <= max_dur_s:
                continue
            identity = str(item.get("clip_uid") or item.get("s3_path") or "")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            merged.append(dict(item))
        result[name] = tuple(merged)
    return result

def _load_rank_cache(_services, 
    path: Path | None = None,
) -> dict[str, tuple[dict[str, Any], ...]] | None:
    path = path or _services._rank_cache_path()
    if not path.exists():
        return None
    try:
        # Legacy pickle caches are discarded, never executed during migration.
        payload = _services.json.loads(path.read_bytes())
        if payload.get("schema") != 3:
            return None
        stamp, buckets = payload["stamp"], payload["buckets"]
        stamp = tuple(tuple(item) for item in stamp)
        narration_scan = payload.get("narration_scan") is True
    except Exception:  # noqa: BLE001 — cache corrompido = recompute
        return None
    if _services._local_narration_catalog() and not narration_scan:
        return None
    if _services._stamp_hides_present_narration(stamp):
        return None
    if stamp != _services._rank_cache_stamp() or not isinstance(buckets, dict):
        return None
    if any(not isinstance(name, str) or not isinstance(items, list)
           or any(not isinstance(item, dict) for item in items)
           for name, items in buckets.items()):
        return None
    return {name: tuple(items) for name, items in buckets.items()}

def _save_rank_cache(_services, 
    buckets: dict[str, tuple[dict[str, Any], ...]],
    path: Path | None = None,
    *,
    narration_scan: bool | None = None,
) -> None:
    # Quem grava o cache desta biblioteca narrada precisa poder relê-lo.
    # narration_scan=False continua sendo o índice só portátil, recusado
    # quando as narrações existem.
    if narration_scan is None:
        narration_scan = _services._local_narration_catalog()
    try:
        payload = _services.json.dumps({"schema": 3, "narration_scan": narration_scan,
                              "stamp": _services._rank_cache_stamp(),
                              "buckets": _services._rank_evidenced(buckets)},
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
        path = path or _services._rank_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + "." + _services.uuid.uuid4().hex + ".tmp")
        try:
            with tmp.open("xb") as handle:
                handle.write(payload)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError:
        if persist_index_requested():
            raise


def _persist_ranked_index(_services, buckets, path):
    """Explicit preparation also saves a warm in-memory index if needed."""
    if not persist_index_requested():
        return
    operation = _services.ego4d._SELECTION_SNAPSHOT.get()
    marker = ("ego4d-persisted-index", str(path))
    if operation is not None and marker in operation:
        return
    if _services._load_rank_cache(path) is None:
        _services._save_rank_cache(buckets, path)
    if operation is not None:
        operation[marker] = True

def _ranked_pools_snapshot(_services, stamp) -> dict[str, tuple[dict[str, Any], ...]]:
    cached = _services._load_rank_cache()
    if cached is not None:
        return _services._rank_evidenced(_services._merge_rank_seed(cached))
    require_prepared_index("Ego4D")
    if _services._local_narration_catalog():
        spans = (_services.ego4d.rank_all_task_spans(min_dur_s=60, max_dur_s=1800)
                 if _services.ego4d.has_timed_narrations() else {})
        official = _services.task_matching.rank_all_tasks(_services._task_candidates())
        evidenced = (
            _services.ego4d.narration_evidence_clips(min_dur_s=60, max_dur_s=1800)
            if _services.ego4d.has_timed_narrations() else {})
        buckets = _services._union_ranked_clips(
            _services._union_ranked_clips(spans, official), evidenced)
        result = _services._rank_evidenced(_services._merge_rank_seed(buckets))
        _services._save_rank_cache(result, narration_scan=True)
        return result
    seed = _services._load_rank_seed()
    if seed and any(seed.values()):
        # Sem narração local, o índice portátil é o piso. Com narração, ele
        # só completa tarefas que a biblioteca não cobre.
        result = _services._rank_evidenced(_services._merge_rank_seed({}))
        _services._save_rank_cache(result, narration_scan=False)
        return result
    spans = _services.ego4d.rank_all_task_spans(min_dur_s=60, max_dur_s=1800) if _services.ego4d.has_timed_narrations() else {}
    official = _services.task_matching.rank_all_tasks(_services._task_candidates())
    evidenced = (
        _services.ego4d.narration_evidence_clips(min_dur_s=60, max_dur_s=1800) if _services.ego4d.has_timed_narrations() else {})
    buckets = _services._union_ranked_clips(
        _services._union_ranked_clips(spans, official), evidenced)
    result = _services._rank_evidenced(_services._merge_rank_seed(buckets))
    _services._save_rank_cache(result, narration_scan=_services._local_narration_catalog())
    return result

def _ranked_pools_cached(_services) -> dict[str, tuple[dict[str, Any], ...]]:
    buckets = _services._ranked_pools_snapshot(_services._rank_cache_stamp())
    _persist_ranked_index(_services, buckets, _services._rank_cache_path())
    return buckets

def _duration_ranked_snapshot(_services, stamp, min_dur_s: float, max_dur_s: float):
    """Recheck indexed source windows once per content-bound duration range."""
    cache_key = _services.hashlib.sha256(
        f"{float(min_dur_s):.6f}|{float(max_dur_s):.6f}".encode("ascii")
    ).hexdigest()[:16]
    path = _services.config.DATA_DIR / f"task_rank_cache_{cache_key}.pkl"
    cached = _services._load_rank_cache(path)
    if cached is not None:
        return _services._rank_evidenced(_services._merge_rank_seed(
            cached, min_dur_s=min_dur_s, max_dur_s=max_dur_s))
    require_prepared_index("Ego4D para esta faixa de duração")
    full = _services._ranked_pools_cached()
    result = {
        name: tuple(clip for clip in rows
                    if min_dur_s <= float(clip.get("dur_s") or 0) <= max_dur_s)
        for name, rows in full.items()
    }
    if _services.ego4d.has_timed_narrations():
        windows = _services._duration_window_candidates(full)
        if windows:
            recuts = _services.ego4d.revalidate_task_windows(
                windows, min_dur_s=min_dur_s, max_dur_s=max_dur_s)
            result = _services._union_ranked_clips(
                result, recuts, min_dur_s=min_dur_s, max_dur_s=max_dur_s)
            result = _services._link_rank_history(result, full)
    result = _services._rank_evidenced(result)
    _services._save_rank_cache(result, path, narration_scan=_services._local_narration_catalog())
    return result

def _duration_window_candidates(_services, buckets) -> dict[str, list[dict[str, Any]]]:
    """Union indexed footage before requesting new verified segmentation.

    Adjacent five-minute scene windows can support a longer requested take.
    Every union is revalidated against current annotations and sensor coverage;
    a gap in the original source windows remains a gap here.
    """
    ranges: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for name, clips in buckets.items():
        name = _services.task_matching.canonical_task_name(name)
        for clip in clips:
            parent = str(clip.get("parent_video_uid") or "")
            window = _services._clip_window(clip)
            if parent and window is not None:
                ranges.setdefault((name, parent), []).append(window)
    candidates: dict[str, list[dict[str, Any]]] = {}
    for (name, parent), windows in ranges.items():
        merged: list[tuple[float, float]] = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        for start, end in merged:
            for relative_start, relative_end in _services.ego4d.split_parent_windows(end - start):
                candidates.setdefault(name, []).append({
                    "parent_video_uid": parent,
                    "window_s": (start + relative_start, start + relative_end),
                })
    return candidates

def _duration_ranked_pools(_services, min_dur_s: float, max_dur_s: float):
    stamp = _services._rank_cache_stamp()
    operation = _services.ego4d._SELECTION_SNAPSHOT.get()
    marker = ("ego4d-prepared-duration", str(_services.config.DATA_DIR), stamp,
              float(min_dur_s), float(max_dur_s))
    if prepared_only_requested() and operation is not None and marker in operation:
        return operation[marker]
    try:
        buckets = _services._duration_ranked_snapshot(stamp, min_dur_s, max_dur_s)
    except CatalogPreparationRequired:
        if not prepared_only_requested():
            raise
        # A missing duration variant does not invalidate the prepared base.
        # Keep whole, already classified clips; new cuts belong to explicit
        # preparation. The base snapshot still rejects changed source/rules.
        full = _services._ranked_pools_snapshot(stamp)
        buckets = {
            name: tuple(clip for clip in rows
                        if min_dur_s <= float(clip.get("dur_s") or 0) <= max_dur_s)
            for name, rows in full.items()
        }
        # A filtered view is not a complete duration index. Cache it only for
        # this read operation, never in the shared LRU or on disk: a later
        # explicit preparation must still discover additional valid cuts.
        if operation is not None:
            operation[marker] = buckets
        return buckets
    if operation is not None:
        operation.pop(marker, None)
    cache_key = _services.hashlib.sha256(
        f"{float(min_dur_s):.6f}|{float(max_dur_s):.6f}".encode("ascii")
    ).hexdigest()[:16]
    _persist_ranked_index(_services, buckets,
        _services.config.DATA_DIR / f"task_rank_cache_{cache_key}.pkl")
    return buckets

def _nymeria_windows(_services, task_name: str, min_dur_s: float,
                     max_dur_s: float, *, catalog_only: bool = False) -> tuple[dict[str, Any], ...]:
    """Nymeria owns a cache bound to exact current roots/media/annotations."""
    try:
        options = {"catalog_only": True} if catalog_only else {}
        clips = _services.nymeria.automatic_candidates(task_name=task_name,
            min_dur_s=min_dur_s, max_dur_s=max_dur_s, include_planned=True, **options)
    except CatalogPreparationRequired:
        raise
    except Exception:
        if persist_index_requested():
            raise
        return ()
    return tuple(dict(clip) for clip in clips)

def _compatible_task_clips(_services, 
    task_name: str,
    dataset_provider: str = "all",
    *,
    min_dur_s: float = 60,
    max_dur_s: float = 1800,
    catalog_only: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Combina fontes compatíveis sem reinterpretar categorias.

    HoloAssist só participa das duas tarefas de móveis explicitamente
    mapeadas; ausência do índice local não afeta o catálogo Ego4D.
    """
    provider = _services.normalize_dataset_provider(dataset_provider)
    ego_clips: tuple[dict[str, Any], ...] = ()
    task_name = _services.task_matching.canonical_task_name(task_name)
    if provider in ("all", "ambos", "ego4d"):
        report_progress("Ego4D: conferindo anotações e índice de categorias…", phase="ego4d_catalog")
        prepared_pool_read = (
            provider in ("all", "ambos")
            and catalog_only
            and not persist_index_requested()
            and not _services._local_narration_catalog()
        )
        if prepared_pool_read:
            try:
                with prepared_catalog_read():
                    pools = _services._ranked_pools()
            except CatalogPreparationRequired:
                pools = {}
        else:
            if not prepared_only_requested():
                _services.ego4d.ensure_task_annotations()
            if ((min_dur_s, max_dur_s) != (60, 1800)
                    and _services.ego4d.has_timed_narrations()):
                with _services._RANK_LOCK:
                    _services._refresh_rank_inputs()
                    pools = _services._duration_ranked_pools(min_dur_s, max_dur_s)
            else:
                pools = _services._ranked_pools()
        if task_name in pools:
            ego_clips = pools[task_name]
        elif not prepared_pool_read and not prepared_only_requested():
            ego_clips = tuple(_services.task_matching.ranked_clips(task_name, _services._task_candidates()))
        ego_clips = tuple(c for c in ego_clips
                          if min_dur_s <= float(c.get("dur_s") or 0) <= max_dur_s)
    if provider == "ego4d":
        return ego_clips
    if provider == "nymeria":
        report_progress("NymeriaPlus: conferindo sequências e duração…", phase="nymeria_catalog")
        return _services._nymeria_windows(task_name, min_dur_s, max_dur_s, catalog_only=catalog_only)
    holo_clips: tuple[dict[str, Any], ...] = ()
    if provider in ("all", "holoassist"):
        try:
            holo_clips = tuple(_services.holoassist.list_clips(
                task_name, min_dur_s=min_dur_s, max_dur_s=max_dur_s))
        except FileNotFoundError:
            holo_clips = ()
    if provider in ("all", "ambos"):
        report_progress("NymeriaPlus: conferindo sequências e duração…", phase="nymeria_catalog")
    nymeria_clips = (_services._nymeria_windows(task_name, min_dur_s, max_dur_s, catalog_only=catalog_only)
                     if provider in ("all", "ambos") else ())
    if provider == "ambos":
        return (*nymeria_clips, *ego_clips)
    return (*holo_clips, *nymeria_clips, *ego_clips)

def _with_cached_expansion(_services, 
    clips: list[dict[str, Any]],
    task_name: str,
    *,
    min_dur_s: float,
    max_dur_s: float,
    work_dir: Path | None = None,
    include_disabled: bool = False,
    catalog_only: bool = False,
) -> list[dict[str, Any]]:
    """Acrescenta cenário já gravado pelo acelerador, sem buscar mídia nova.

    Restaurado de v1.0.73/v2.0.2: o rank narrado sozinho deixava clipes
    `_native` prontos fora do pool. Melhor aproveitamento do Ego4D em disco.
    """
    from ..ego_accelerator import ready_scenario_clips

    merged = list(clips)
    seen = {str(clip.get("clip_uid") or "") for clip in merged}
    options = {"catalog_only": True} if catalog_only else {}
    for extra in ready_scenario_clips(
            task_name, min_dur_s=min_dur_s, max_dur_s=max_dur_s,
            work_dir=work_dir, allow_disabled=include_disabled, **options):
        uid = str(extra.get("clip_uid") or "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        merged.append(extra)
    return merged

def _clip_is_cached(_services, clip: dict[str, Any], work_dir: Path) -> bool:
    if str(clip.get("source") or "") == "holoassist":
        from ..holo_accelerator import clip_ready
        return clip_ready(clip, work_dir)
    if str(clip.get("source") or "") == "nymeria" or str(
            clip.get("clip_uid") or "").startswith("nymeria:"):
        stem = "nymeria_" + str(clip.get("clip_uid") or "").replace(
            ":", "_").replace(".", "_")
        return (_services.Path(work_dir) / f"{stem}_native.mp4").is_file()
    return _services.ego_clip_cache_state(clip, work_dir) == "ready"

def _catalog_clip_cached_hint(_services, clip: dict[str, Any], work_dir: Path) -> bool:
    """Read inventory/encode metadata only; this never admits a media send.

    Category counts do not need to hash a parent MP4 for each of its windows.
    The actual cache and source bytes are checked by prepare/upload as before.
    A same-size replacement can therefore retain this hint until that gate.
    """
    try:
        work = _services.Path(work_dir)
        source_name = str(clip.get("source") or "ego4d")
        if source_name == "nymeria":
            if clip.get("acquisition_required") is True:
                return False
            uid = str(clip.get("clip_uid") or "")
            if not uid.startswith("nymeria:"):
                return False
            stem = "nymeria_" + uid.replace(":", "_").replace(".", "_")
            seq_dir = _services.Path(str(clip.get("path") or ""))
            paths = (work / f"{stem}_native.mp4",
                     seq_dir / "recording_head/data/data.vrs",
                     seq_dir / "recording_head/data/motion.vrs")
            return all(path.is_file() and path.stat().st_size > 0 for path in paths)
        if source_name == "holoassist":
            from ..holo_accelerator import native_path, source_path, sensors_ready
            source, native = source_path(clip), native_path(clip, work)
            if not sensors_ready(clip):
                return False
            start_s = dur_s = None
        else:
            # Ranked candidates and official rows carry source coordinates.
            # Do not resolve incomplete inventory through sync_meta here.
            row = dict(clip)
            uid = str(row.get("exported_clip_uid") or row.get("clip_uid") or "")
            if not uid:
                return False
            window = row.get("window_s")
            if isinstance(window, (list, tuple)) and len(window) == 2:
                row.update(parent_start_sec=window[0], parent_end_sec=window[1])
            elif "parent_start_sec" not in row or "parent_end_sec" not in row:
                return False
            row["exported_clip_uid"] = uid
            plan = _services._ego_prepare_plan(row)
            source, native = work / plan["source_name"], work / plan["native_name"]
            imu = work / plan["imu_name"]
            if not imu.is_file() or imu.stat().st_size <= 128:
                return False
            start_s, dur_s = plan["norm_start"], plan["dur_s"]
        if not source.is_file() or not native.is_file():
            return False
        source_stat, native_stat = source.stat(), native.stat()
        if min(source_stat.st_size, native_stat.st_size) <= 1024 * 1024:
            return False
        marker = native.with_name(native.name + ".source.json")
        saved = _services.json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            return False
        expected = {"version": _services._NATIVE_CACHE_VERSION,
                    "source_size": source_stat.st_size,
                    "source_mtime_ns": source_stat.st_mtime_ns,
                    "prepared_size": native_stat.st_size,
                    "start_s": None if start_s is None else round(float(start_s), 6),
                    "dur_s": None if dur_s is None else round(float(dur_s), 6),
                    "width": 1440, "height": 1080, "fps": 30}
        return (all(saved.get(key) == value and type(saved.get(key)) is type(value)
                    for key, value in expected.items())
                and all(isinstance(saved.get(key), str)
                        and _services.re.fullmatch(r"[0-9a-f]{64}", saved[key])
                        for key in ("source_sha256", "prepared_sha256")))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return False

def _catalog_queue_accepts(_services, clip: dict[str, Any], task_name: str) -> bool:
    """Check current catalog declarations without repeating Nymeria planning.

    These rows were produced by the provider's read-only catalog operation.
    They are estimates, never a substitute for the fresh preparation gate.
    """
    if clip.get("source") != "nymeria":
        return _services._prepare_queue_accepts(clip, task_name, fresh=False)
    try:
        name = _services.task_matching.canonical_task_name(task_name)
        carrier = clip["selection_evidence"]
        task = carrier["task"]
        if (carrier.get("schema") != 1 or carrier.get("dataset") != "nymeria"
                or task.get("name") != name or clip.get("task_name_authoritative") != name
                or _services.task_matching.rule_for(name) is None):
            return False
        planned = clip.get("acquisition_required") is True
        algorithm = _services.nymeria._PLANNED_ALGORITHM if planned else _services.nymeria._ALGORITHM
        window = _services._clip_window(clip)
        duration = float(clip["dur_s"])
        if (carrier.get("algorithm") != algorithm or window is None
                or not _services.math.isfinite(duration) or duration <= 0
                or abs((window[1] - window[0]) - duration) > 1e-6):
            return False
        bounds = carrier["duration_bounds_s"]
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or not float(bounds[0]) <= duration <= float(bounds[1])):
            return False
        if planned:
            device_window = clip.get("planned_device_window_ns")
            return (carrier.get("sensor_coverage") == "unmeasured"
                    and clip.get("selection_ready") is False
                    and device_window == clip.get("device_window_ns")
                    and device_window == carrier.get("planned_device_window_ns")
                    and clip.get("clip_uid") ==
                    f"nymeria-planned:{clip['seq_id']}:{device_window[0]}:{device_window[1]}")
        return str(clip.get("clip_uid") or "").startswith("nymeria:")
    except (KeyError, ValueError, TypeError, AttributeError):
        return False

def _prefer_cached_clips(_services, 
    clips: list[dict[str, Any]], work_dir: Path, *,
    prioritize: bool = True,
) -> list[dict[str, Any]]:
    """Prefer prepared media and acquired originals without admitting either.

    Dataset mode can reuse originals already downloaded on demand. File
    presence only orders acquisition; preparation still checks source bytes,
    SDK clocks and sensor coverage before any upload.
    """
    ordered = list(clips)
    ready: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []
    in_original_order: list[dict[str, Any]] = []
    for clip in ordered:
        if _services._clip_is_cached(clip, work_dir):
            marked = {**clip, "_cache_ready_at_selection": True}
            ready.append(marked)
            in_original_order.append(marked)
        else:
            later.append(clip)
            in_original_order.append(clip)
    def originals_present(clip):
        if clip.get("source") != "nymeria" or not clip.get("path"):
            return False
        try:
            data = _services.Path(clip["path"]) / "recording_head" / "data"
            return all((data / name).is_file() and (data / name).stat().st_size > 0
                       for name in ("data.vrs", "motion.vrs"))
        except (OSError, TypeError, ValueError):
            return False

    remaining = later if prioritize else in_original_order
    acquired, remote = [], []
    for clip in remaining:
        (acquired if originals_present(clip) else remote).append(clip)
    return (ready if prioritize else []) + acquired + remote

def automatic_candidates(_services, tsk: TaskSpec, config: CampaignConfig, *, catalog_only: bool = False) -> list[dict[str, Any]]:
    """Resolve the candidate pool once; a reviewed pool is reused verbatim."""
    if config.candidate_plan is not None:
        if tsk.task_id not in config.candidate_plan:
            raise ValueError("categoria ausente do plano confirmado")
        return [dict(clip) for clip in config.candidate_plan[tsk.task_id]
                if not tsk.requires_audio or _services._candidate_audio_possible(clip)]
    dataset_provider = _services.normalize_dataset_provider(config.dataset_provider)
    providers = _services.provider_ids_for(dataset_provider)
    content_mode = _services.normalize_content_mode(config.content_mode)
    work_dir = _services.Path(config.work_dir)
    shorts: list[dict[str, Any]] = []
    if "ego4d" in providers:
        if tsk.task_name and _services.task_matching.rule_for(tsk.task_name):
            # A mesma seleção usada pela tela inclui o índice
            # portátil mesmo quando há narrações locais parciais.
            shorts = list(_services._compatible_task_clips(
                tsk.task_name, "ego4d", min_dur_s=tsk.min_dur_s,
                max_dur_s=tsk.max_dur_s))
            # Best of v1.0.73/v2.0.2: merge accelerator-ready Ego4D so
            # already-normalized cache is not left unused by the rank pool.
            if content_mode != "dataset":
                shorts = _services._with_cached_expansion(
                    shorts, tsk.task_name, min_dur_s=tsk.min_dur_s,
                    max_dur_s=tsk.max_dur_s, work_dir=work_dir,
                    include_disabled=True, **({"catalog_only": True} if catalog_only else {}))
        else:
            shorts = _services.ego4d.list_clips(
                scenario=tsk.scenario,
                min_dur_s=tsk.min_dur_s, max_dur_s=tsk.max_dur_s,
                gopro_minor=tsk.gopro_minor, max_results=None)
    if tsk.task_name and "holoassist" in providers:
        try:
            strict_holoassist = _services.holoassist.list_clips(
                tsk.task_name,
                min_dur_s=tsk.min_dur_s,
                max_dur_s=tsk.max_dur_s,
            )
        except FileNotFoundError:
            strict_holoassist = []
        # Fonte complementar primeiro: são sessões inteiras de uma
        # tarefa rotulada, não inferências por texto de narração.
        shorts = [*strict_holoassist, *shorts]
    if "nymeria" in providers:
        try:
            nymeria_clips = _services.nymeria.automatic_candidates(
                task_name=tsk.task_name, task_id=tsk.task_id,
                registry_key=tsk.registry_key,
                min_dur_s=tsk.min_dur_s, max_dur_s=tsk.max_dur_s,
                include_planned=content_mode != "cache",
                **({"catalog_only": True} if catalog_only else {}))
        except CatalogPreparationRequired:
            raise
        except Exception:
            nymeria_clips = []
        # Nymeria windows carry current action and measured VRS evidence.
        if providers == ("nymeria",):
            shorts = list(nymeria_clips)
        else:
            shorts = [*nymeria_clips, *shorts]
    if content_mode == "cache":
        cached = _services._catalog_clip_cached_hint if catalog_only else _services._clip_is_cached
        shorts = [clip for clip in shorts if cached(clip, work_dir)]
    elif not catalog_only:
        from ..imu_coverage import refine_candidates
        # refine_candidates is Ego4D-IMU-CSV specific; keep Nymeria intact.
        ego: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []
        for clip in shorts:
            uid = str(clip.get("clip_uid") or "")
            if clip.get("source") == "nymeria" or uid.startswith("nymeria:"):
                other.append(clip)
            else:
                ego.append(clip)
        ego = refine_candidates(ego, work_dir, tsk.min_dur_s, tsk.max_dur_s)
        shorts = [*ego, *other]
    task_name = tsk.task_name
    if catalog_only:
        return [dict(clip, requires_measured_validation=True,
                     capacity_kind=clip.get("capacity_kind", "estimate_before_preparation"))
                for clip in shorts if (not tsk.requires_audio or _services._candidate_audio_possible(clip))
                and _services._catalog_queue_accepts(clip, task_name)]
    return [clip for clip in shorts
            if (not tsk.requires_audio or _services._candidate_audio_possible(clip))
            and _services._prepare_queue_accepts(clip, task_name, fresh=False)]

def available_tasks(_services, email: str, org_key: str, *, min_dur_s: float = 60,
                    max_dur_s: float = 1800,
                    include_unavailable: bool = False,
                    dataset_provider: str = "all",
                    content_mode: str = "both",
                    session: Session | None = None,
                    remote_tasks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Devolve as tasks do Minute que têm mídia elegível com IMU real.

    Cada item traz clip_count e dur_range_s já calculados para a faixa pedida.
    Com include_unavailable, tarefas compatíveis apenas fora da faixa pedida
    também são devolvidas para a UI explicar por que estão desabilitadas.
    `session` reusa a Session já autenticada (o GET /api/tasks não pode
    refreshar o Firebase duas vezes na mesma conta).
    """
    if remote_tasks is None:
        sess = session
        _services._bind_session_email(sess, email)
        with _services.account_registration_route(email):
            if sess is None:
                sess = _services.Session.from_email(email)
                _services._bind_session_email(sess, email)
                sess.ensure_auth(org_key=org_key)
            elif not getattr(sess, "_live", False):
                sess.ensure_auth(org_key=org_key)
            tasks = sess.all_tasks(org_key)
    else:
        tasks = remote_tasks
    tasks = _services.validate_task_catalog(tasks)
    mode = _services.normalize_content_mode(content_mode)
    ready_by_uid: dict[str, bool] = {}
    eligibility: dict[tuple[str, str], bool] = {}

    def queue_accepts(clip: dict[str, Any], name: str) -> bool:
        # Both duration pools share the request's immutable parser inputs.
        # Include the whole candidate/evidence and task, never only its UID.
        key = (_services.task_matching.canonical_task_name(name), _services.ego4d._selection_digest(clip))
        if key not in eligibility:
            eligibility[key] = _services._catalog_queue_accepts(clip, name)
        return eligibility[key]

    def cache_ready(clip: dict[str, Any]) -> bool:
        uid = str(clip.get("clip_uid") or "")
        if uid not in ready_by_uid:
            ready_by_uid[uid] = _services._catalog_clip_cached_hint(
                clip, _services.config.MEDIA_DATA_DIR / "ego4d")
        return ready_by_uid[uid]

    out = []
    for task_index, t in enumerate(tasks):
        name = (t.get("name") or "").strip()
        checkpoint()
        report_progress(f"Conferindo categoria {task_index + 1}/{len(tasks)}: "
                        + _services.TASK_NAME_PT.get(name, name), phase="campaign_categories",
                        completed=task_index, total=len(tasks))
        requires_audio = t.get("requiresAudio", False)
        rule = _services.task_matching.rule_for(name)
        if not rule:
            if include_unavailable:
                out.append({
                    "id": t.get("id"), "name": name, "name_pt": name,
                    "description": str(t.get("description") or ""),
                    "requires_audio": requires_audio,
                    "clip_count": 0, "clip_sources": {}, "overall_clip_count": 0,
                    "available_for_duration": False, "mapping_supported": False,
                    "unavailable_reason": "Esta tarefa ainda não tem uma regra de seleção no QMoney.",
                })
            continue
        all_clips = [c for c in _services._compatible_task_clips(name, dataset_provider, catalog_only=True)
                     if 60 <= c["dur_s"] <= 1800]
        clips = list(_services._compatible_task_clips(
            name, dataset_provider, min_dur_s=min_dur_s, max_dur_s=max_dur_s, catalog_only=True))
        if (mode != "dataset"
                and _services.normalize_dataset_provider(dataset_provider) in ("all", "ambos", "ego4d")):
            all_clips = _services._with_cached_expansion(
                all_clips, name, min_dur_s=60, max_dur_s=1800,
                include_disabled=True, catalog_only=True)
            clips = _services._with_cached_expansion(
                clips, name, min_dur_s=min_dur_s, max_dur_s=max_dur_s,
                include_disabled=True, catalog_only=True)
        if mode == "cache":
            all_clips = [clip for clip in all_clips if cache_ready(clip)]
            clips = [clip for clip in clips if cache_ready(clip)]
        report_progress(f"Categoria {task_index + 1}/{len(tasks)}: validando compatibilidade dos trechos…",
                        phase="campaign_category_evidence")
        all_clips = [clip for clip in all_clips if queue_accepts(clip, name)]
        clips = [clip for clip in clips if queue_accepts(clip, name)]
        audio_blocked = requires_audio and any(not _services._candidate_audio_possible(c) for c in all_clips)
        if requires_audio:
            all_clips = [c for c in all_clips if _services._candidate_audio_possible(c)]
            clips = [c for c in clips if _services._candidate_audio_possible(c)]
        if clips or include_unavailable:
            source_counts: dict[str, int] = {}
            for clip in clips:
                source = str(clip.get("source") or "ego4d")
                source_counts[source] = source_counts.get(source, 0) + 1
            categories = t.get("categories") or []
            category = categories[0] if categories else {}
            category_slug = str(category.get("slug") or "other")
            out.append({
                "id": t.get("id"), "name": name,
                "description": str(t.get("description") or ""),
                "requires_audio": requires_audio,
                # scenario permanece por compatibilidade; a seleção nova usa rule.
                "scenario": rule.primary[0],
                "name_pt": _services.TASK_NAME_PT.get(name, _services.TASK_NAME_PT.get(
                    _services.task_matching.canonical_task_name(name), name)),
                "boosted": name in _services.BOOSTED_TASKS,
                "category_slug": category_slug,
                "category_label": _services.CATEGORY_PT.get(
                    category_slug, str(category.get("label") or "Outras")),
                "clip_count": len(clips),
                **_services.diversity_summary(clips),
                "clip_sources": source_counts,
                "dur_range_s": ((min(c["dur_s"] for c in clips),
                                 max(c["dur_s"] for c in clips)) if clips else None),
                "overall_clip_count": len(all_clips),
                "overall_dur_range_s": ((min(c["dur_s"] for c in all_clips),
                                         max(c["dur_s"] for c in all_clips))
                                        if all_clips else None),
                "available_for_duration": bool(clips),
                "mapping_supported": True,
                "requires_measured_validation": True,
                "unavailable_reason": ("" if clips else
                    "Esta tarefa exige áudio real; o conteúdo deste provedor não possui áudio disponível."
                    if audio_blocked and not all_clips else
                    "Há conteúdo no catálogo, mas nenhum trecho nesta faixa de duração."
                    if all_clips else
                    "As anotações Ego4D ainda não estão disponíveis. Configure o acesso em Integrações e atualize as categorias."
                    if _services.normalize_dataset_provider(dataset_provider) == "ego4d"
                    and not _services._local_narration_catalog() else
                    "Nenhum trecho atende à atividade e aos sensores no provedor escolhido."),
                "match_confidence": rule.confidence,
                "match_scenarios": list(rule.primary),
            })
    return out

REQUIRES = ('BOOSTED_TASKS', 'CATEGORY_PT', 'Path', 'Session', 'TASK_NAME_PT', '_NATIVE_CACHE_VERSION', '_RANK_LOCK', '_bind_session_email', '_candidate_audio_possible', '_catalog_clip_cached_hint', '_catalog_queue_accepts', '_clip_is_cached', '_clip_window', '_compatible_task_clips', '_duration_ranked_pools', '_duration_ranked_snapshot', '_duration_window_candidates', '_ego_prepare_plan', '_link_rank_history', '_load_rank_cache', '_load_rank_seed', '_local_narration_catalog', '_merge_rank_seed', '_narration_file_present', '_nymeria_windows', '_prepare_queue_accepts', '_rank_cache_path', '_rank_cache_stamp', '_rank_evidenced', '_rank_seed_bytes_cached', '_rank_seed_path', '_ranked_pools', '_ranked_pools_cached', '_ranked_pools_snapshot', '_refresh_rank_inputs', '_same_action_already_covered', '_save_rank_cache', '_stamp_hides_present_narration', '_task_candidates', '_union_ranked_clips', '_with_cached_expansion', 'account_registration_route', 'config', 'diversity_summary', 'ego4d', 'ego_clip_cache_state', 'gzip', 'hashlib', 'holoassist', 'json', 'math', 'normalize_content_mode', 'normalize_dataset_provider', 'nymeria', 'provider_ids_for', 're', 'task_matching', 'uuid', 'validate_task_catalog')
