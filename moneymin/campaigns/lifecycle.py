"""Managed media cleanup and lifecycle policy for campaigns."""
from __future__ import annotations

def _within(_services, path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

def _delete_media_files(_services, 
    paths: list[Path],
    *,
    allowed_roots: tuple[Path, ...],
    protection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Remove somente arquivos validados dentro dos diretórios de mídia.

    A lista é interna, mas ainda assim cada caminho é resolvido e confinado.
    Falha de limpeza nunca transforma um upload confirmado em falha.
    """
    roots = tuple(root.resolve() for root in allowed_roots)
    if protection is None:
        from ..recovery import media_cleanup_protection
        protection = media_cleanup_protection()
    protected_keys = {_services.os.path.normcase(str(_services.Path(p).resolve())) for p in protection['paths']}
    protected_hashes = protection['sha256']
    unique: dict[str, _services.Path] = {}
    skipped = 0
    for raw in paths:
        candidate = _services.Path(raw)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            skipped += 1
            continue
        if not any(_services._within(resolved, root) for root in roots):
            skipped += 1
            continue
        if _services.os.path.normcase(str(resolved)) in protected_keys:
            skipped += 1
            continue
        if protected_hashes and resolved.is_file():
            try:
                if _services._native_cache_fingerprint(resolved, allow_empty=True)[1] in protected_hashes:
                    skipped += 1
                    continue
            except (OSError,RuntimeError,ValueError):
                skipped += 1
                continue
        unique[_services.os.path.normcase(str(resolved))] = candidate

    removed = freed = 0
    errors: list[str] = []
    parents: set[_services.Path] = set()
    for candidate in unique.values():
        try:
            if not candidate.is_file() and not candidate.is_symlink():
                continue
            size = candidate.stat().st_size if candidate.is_file() else 0
            parents.add(candidate.parent)
            candidate.unlink(missing_ok=True)
            removed += 1
            freed += size
        except OSError as exc:
            errors.append(f"{candidate.name}: {exc}")

    # Sensores HoloAssist vivem em subpastas; retire apenas diretórios vazios e
    # nunca o próprio diretório-raiz permitido.
    for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
        current = parent
        while True:
            try:
                resolved = current.resolve(strict=False)
            except OSError:
                break
            matching_root = next(
                (root for root in roots if _services._within(resolved, root)), None
            )
            if matching_root is None or resolved == matching_root:
                break
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    return {
        "files": removed,
        "bytes": freed,
        "errors": errors,
        "skipped": skipped,
    }

def _media_companions(_services, path: Path) -> list[Path]:
    """Marcadores e temporários associados a um MP4 preparado."""
    if path.suffix.lower() != ".mp4":
        return []
    return [
        path.with_name(path.name + ".ok"),
        path.with_name(path.name + ".source.json"),
        path.with_name(path.name + ".source.json.tmp"),
        path.with_name(f"{path.stem}.tmp.mp4"),
        path.with_name(path.name + ".tmp"),
        path.with_name(path.name + ".part"),
        path.with_name(path.name + ".chunk.ok"),
    ]

def _expected_prefetch_paths(_services, 
    clip_info: dict[str, Any],
    work_dir: Path,
) -> list[Path]:
    """Prevê os caminhos usados pelo próximo preparo para evitar uma corrida."""
    work = _services.Path(work_dir)
    if clip_info.get("source") == "holoassist":
        video_name = str(
            clip_info.get("video_name") or clip_info.get("video_uid") or ""
        )
        if not video_name:
            return []
        recording = _services.holoassist.data_dir() / "recordings" / video_name
        safe_stem = "holoassist_" + video_name.replace("/", "_").replace("\\", "_")
        paths = [
            recording / "Video_pitchshift.mp4",
            recording / "Video_compress.mp4",
            recording / "IMU" / "Accelerometer_sync.txt",
            recording / "IMU" / "Gyroscope_sync.txt",
            recording / "IMU" / "Magnetometer_sync.txt",
            work / f"{safe_stem}_native.mp4",
        ]
    elif clip_info.get("source") == "nymeria":
        clip_uid = str(clip_info.get("clip_uid") or "")
        stem = "nymeria_" + clip_uid.replace(":", "_").replace(".", "_")
        seq_dir = _services.Path(str(clip_info.get("path") or ""))
        paths = [work / f"{stem}_native.mp4"]
        if seq_dir.is_absolute():
            paths.extend(seq_dir / "recording_head" / "data" / name
                         for name in ("data.vrs", "motion.vrs"))
    else:
        clip_uid = str(
            clip_info.get("exported_clip_uid") or clip_info.get("clip_uid") or ""
        )
        parent_uid = str(clip_info.get("parent_video_uid") or clip_uid)
        media_uid = (
            str(clip_info.get("media_uid") or parent_uid)
            if clip_info.get("needs_cut") else clip_uid
        )
        if not clip_uid or not media_uid:
            return []
        paths = [
            work / f"{media_uid}.mp4",
            work / f"{clip_uid}_native.mp4",
            work / f"{parent_uid}_imu.csv",
        ]
    expanded = list(paths)
    for path in paths:
        expanded.extend(_services._media_companions(path))
    return expanded

def _cleanup_uploaded_item(_services, 
    item: dict[str, Any],
    work_dir: Path,
    *,
    protected_paths: set[Path] | None = None,
) -> dict[str, Any]:
    """Release only application-owned bytes after the batch is confirmed."""
    from ..media_lifecycle import cleanup_managed_media, managed_media_paths
    candidates = [_services.Path(value) for value in item.get("_cleanup_paths", [])
                  if isinstance(value, str) and value]
    base_value = item.get("video_path")
    base = _services.Path(base_value) if isinstance(base_value, str) and base_value else None
    if base is not None:
        candidates.append(base)
        try:
            variants = [
                path for path in base.parent.glob("*_acc*.mp4")
                if path.name.startswith(base.stem + "_acc")
            ]
            chunk_variants = [
                *base.parent.glob(f"{base.stem}_ch*{base.suffix}"),
                *base.parent.glob(f"{base.stem}_acc*_ch*{base.suffix}"),
            ]
        except OSError:
            variants = []
            chunk_variants = []
        candidates.extend(variants)
        candidates.extend(chunk_variants)
    protected_keys: set[str] = set()
    for protected in protected_paths or set():
        try:
            protected_keys.add(_services.os.path.normcase(str(protected.resolve(strict=False))))
        except OSError:
            continue
    filtered: list[_services.Path] = []
    protected_count = 0
    for candidate in candidates:
        try:
            key = _services.os.path.normcase(str(candidate.resolve(strict=False)))
        except OSError:
            key = ""
        if key and key in protected_keys:
            protected_count += 1
            continue
        filtered.append(candidate)
    result = cleanup_managed_media(
        filtered,
        allowed_roots=(_services.Path(work_dir), _services.holoassist.data_dir() / "recordings"),
        protected_paths=protected_paths or set(),
    )
    # A source/encode marker describes a present MP4. Remove it only after
    # that specific media was released; protected and foreign bytes keep it.
    companions = [companion for raw in result["removed_paths"]
                  for companion in _services._media_companions(_services.Path(raw))]
    companion_result = _services._delete_media_files(
        companions, allowed_roots=(_services.Path(work_dir), _services.holoassist.data_dir() / "recordings"))
    result["files"] += companion_result["files"]
    result["bytes"] += companion_result["bytes"]
    result["errors"].extend(companion_result["errors"])
    if item.get("source") == "nymeria" and item.get("seq_id"):
        from .. import nymeria_library
        candidate = item.get("_content_candidate") or {}
        seq_dir = _services.Path(str(candidate.get("path") or ""))
        if seq_dir.is_absolute():
            released = nymeria_library.cleanup_sequence_sources(
                str(item["seq_id"]), root=seq_dir.parent,
                protected_paths=protected_paths or set())
            result["files"] += released["files"]
            result["bytes"] += released["bytes"]
            result["errors"].extend(released["errors"])
            candidates.extend(_services.Path(row["path"]) for row in (item.get("_content_inputs") or {}).values()
                              if isinstance(row, dict) and isinstance(row.get("path"), str))
    roots = [_services.Path(work_dir), _services.holoassist.data_dir() / "recordings"]
    candidate = item.get("_content_candidate") or {}
    if item.get("source") == "nymeria" and isinstance(candidate.get("path"), str):
        seq_dir = _services.Path(candidate["path"])
        if seq_dir.is_absolute():
            roots.append(seq_dir.parent)
    retained = managed_media_paths(candidates, allowed_roots=tuple(roots))
    result["retained_managed"] = len(retained)
    result["retained_bytes"] = sum(path.stat().st_size for path in retained)
    result["retained_names"] = [path.name for path in retained]
    result["protected"] = protected_count
    return result

def _cleanup_rejected_prepare(_services, clip: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Release an unconsumed candidate; journals still protect every binding."""
    paths = _services._expected_prefetch_paths(clip, work_dir)
    return _services._cleanup_uploaded_item({"source": clip.get("source"),
                                   "seq_id": clip.get("seq_id"),
                                   "_content_candidate": clip,
                                   "_cleanup_paths": [str(path) for path in paths]}, work_dir)

def _cleanup_stopped_prepared_media(_services, 
    item: dict[str, Any] | None,
    clip: dict[str, Any] | None,
    work_dir: Path,
    prefetch: _ClipPrefetch,
    *,
    cleanup_candidate: bool = True,
) -> list[dict[str, Any]]:
    """Join prefetch consumers, then release only owned, unreferenced media."""
    prefetch_items = prefetch.cleanup_candidates()
    prefetch.shutdown()
    from .. import recovery
    reconciled = recovery.reconcile_confirmed(refresh=False)
    results = []
    if reconciled.get("archives_removed") or reconciled.get("archive_cleanup_errors"):
        results.append({"files": int(reconciled.get("archives_removed") or 0),
                        "bytes": int(reconciled.get("archive_bytes_removed") or 0),
                        "errors": ["Limpeza de ZIP antigo não terminou; arquivo preservado."
                                   for _error in reconciled.get("archive_cleanup_errors") or []],
                        "protected": int(reconciled.get("archives_retained") or 0)})
    if cleanup_candidate:
        clip = clip or {}
        results.append(_services._cleanup_uploaded_item(item, work_dir) if item is not None
                       else _services._cleanup_rejected_prepare(clip, work_dir))
    for prefetched_clip, paths in prefetch_items:
        if not paths:
            continue
        prefetched_clip = prefetched_clip or {}
        results.append(_services._cleanup_uploaded_item({
            "source": prefetched_clip.get("source"),
            "seq_id": prefetched_clip.get("seq_id"),
            "_content_candidate": prefetched_clip,
            "_cleanup_paths": [str(path) for path in paths],
        }, work_dir))
    return results

def cleanup_media_cache(_services, work_dir: Path | None = None, *, provider: str = "all") -> dict[str, Any]:
    """Limpa downloads/derivados, preservando catálogos e estado da campanha."""
    if provider not in {"all", "ego4d", "holoassist", "nymeria"}:
        raise ValueError("provedor inválido (ego4d|holoassist|nymeria|all)")
    work = _services.Path(work_dir or _services.config.MEDIA_DATA_DIR / "ego4d")
    from ..recovery import media_cleanup_protection
    protection = media_cleanup_protection()  # Validate before deleting one file.
    candidates: list[_services.Path] = []
    if work.is_dir():
        for path in work.iterdir():
            if not path.is_file() and not path.is_symlink():
                continue
            name = path.name.lower()
            belongs_to_holo = name.startswith("holoassist_")
            if provider != "all" and belongs_to_holo != (provider == "holoassist"):
                continue
            if (
                path.suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm"}
                or name.endswith((
                    "_imu.csv",
                    ".mp4.ok",
                    ".mp4.source.json",
                    ".mp4.source.json.tmp",
                    ".part",
                ))
                or ".tmp." in name
            ):
                candidates.append(path)
    recordings = _services.holoassist.data_dir() / "recordings"
    if provider in {"all", "holoassist"} and recordings.is_dir():
        candidates.extend(
            path for path in recordings.rglob("*")
            if path.is_file() or path.is_symlink()
        )
    # A filename/extension is not proof that a manually supplied file belongs
    # to this cache. Only a content-bound prepared-cache marker permits manual
    # cleanup; ambiguous legacy downloads/sensors remain for explicit review.
    owned = []
    for path in candidates:
        if path.suffix.lower() != '.mp4' or path.is_symlink():
            continue
        marker = path.with_name(path.name + '.source.json')
        try:
            saved = _services.json.loads(marker.read_text(encoding='utf8'))
            if (type(saved) is not dict or type(saved.get('version')) is not int
                    or saved['version'] != _services._NATIVE_CACHE_VERSION
                    or not isinstance(saved.get('source_sha256'),str)
                    or not _services.re.fullmatch('[0-9a-f]{64}',saved['source_sha256'])
                    or type(saved.get('prepared_size')) is not int
                    or saved['prepared_size'] != path.stat().st_size
                    or saved.get('prepared_sha256') != _services._native_cache_fingerprint(path)[1]):
                continue
        except (OSError,ValueError,RuntimeError):
            continue
        if _services.os.path.normcase(str(path.resolve())) in {_services.os.path.normcase(str(p)) for p in protection['paths']}:
            continue
        if saved['prepared_sha256'] in protection['sha256']:
            continue
        owned.extend([path,marker])
        # Delete only owned companions; arbitrary .part/tmp/CSV names do not
        # establish ownership or link a file to the admitted cache entry.
        ok = path.with_name(path.name + '.ok')
        if ok.is_file() and not ok.is_symlink():
            owned.append(ok)
    result = _services._delete_media_files(
        owned,
        allowed_roots=(work, recordings),
        protection=protection,
    )
    result['preserved_unowned'] = len(candidates)-len([p for p in owned if p.suffix.lower()=='.mp4'])
    return result

def _all_pending_uploads_succeeded(_services, 
    pending_accounts: list[AccountSpec],
    results: dict[str, dict[str, Any]],
) -> bool:
    """Só libera a mídia quando nenhuma conta enviada ficou pendente."""
    return bool(pending_accounts) and all(
        bool(results.get(account.email, {}).get("ok"))
        for account in pending_accounts
    )

def _cleanup_confirmed_account_media(_services, 
    item: dict[str, Any], account: AccountSpec, result: dict[str, Any],
    paths: list[str], work_dir: Path, *, protected_paths: set[Path],
) -> dict[str, Any] | None:
    """Release an account variant only after its durable receipt is complete."""
    if (result.get("ok") is not True or result.get("finalized") is not True
            or not isinstance(result.get("session_id"), str) or not paths):
        return None
    from .. import recovery
    from ..media_lifecycle import cleanup_managed_media
    groups = recovery._groups(include_reconciled=True)
    rows = next((rows for rows in groups if rows[0]["session_id"] == result["session_id"]
                 and rows[0]["account_email"] == account.email
                 and rows[0]["org_key"] == account.org_key), None)
    if (not rows or not recovery._complete_chunk_group(rows)
            or not all(_services.journal_delivery_confirmed(row)
                       and row.get("campaign_reconciled") is True for row in rows)):
        return None
    from ..campaign_evidence import publication_index, publication_registered
    if not publication_registered(rows, publication_index()):
        return None
    base = _services.Path(item["video_path"])
    # Common native/chunk media serves the other accounts in this item. Only
    # account variants are released early; the batch releases shared media.
    candidates = [_services.Path(path) for path in paths
                  if _services.Path(path).parent == base.parent
                  and _services.Path(path).name.startswith(base.stem + "_acc")]
    variant = _services.Path(paths[0])
    if any(_services.os.path.normcase(str(variant.resolve())) == _services.os.path.normcase(str(consumer.resolve()))
           for consumer in protected_paths):
        # The next/current account can be cutting this same device variant
        # before it has published a journal. Keep its complete chunk family.
        return None
    guards = set(protected_paths)
    for path in candidates:
        if any(path.parent == consumer.parent
               and path.name.startswith(consumer.stem + "_ch")
               for consumer in protected_paths):
            guards.add(path)
    released = cleanup_managed_media(candidates, allowed_roots=(_services.Path(work_dir),),
                                     protected_paths=guards)
    companions = [companion for raw in released["removed_paths"]
                  for companion in _services._media_companions(_services.Path(raw))]
    extra = _services._delete_media_files(companions, allowed_roots=(_services.Path(work_dir),))
    released["files"] += extra["files"]
    released["bytes"] += extra["bytes"]
    released["errors"].extend(extra["errors"])
    return released

def _enforce_account_video_cache(_services, work_dir: Path) -> tuple[int, int]:
    """Apaga variantes `_acc*.mp4` mais antigas se o cache passar do teto.

    Não mexe no `_native.mp4` (fonte do re-encode) nem no original Ego4D.
    Arquivos recém-gerados têm mtime novo e saem por último. Journals e
    reservas pendentes protegem as variantes mesmo acima do teto do cache.
    """
    if not work_dir.exists():
        return 0, 0
    budget = int(float(getattr(_services.config, "VIDEO_CACHE_GB", 40.0)) * 1024 ** 3)
    if budget <= 0:
        return 0, 0
    files: list[tuple[float, int, Path]] = []
    total = 0
    for path in work_dir.glob("*_native_acc*.mp4"):
        name = path.name
        if (not path.is_file() or name.endswith(".tmp")
                or ".tmp." in name):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, path))
        total += st.st_size
    if total <= budget:
        return 0, 0
    from ..recovery import media_cleanup_protection
    protection = media_cleanup_protection()
    files.sort()  # mais antigo primeiro
    removed = freed = 0
    for _mtime, size, path in files:
        if total <= budget:
            break
        with _services._lock_for_account_video(path):
            cleanup = _services._delete_media_files([path], allowed_roots=(work_dir,), protection=protection)
            if cleanup["files"] != 1:
                continue
            # Keep the companion while a protected variant survives. The
            # enclosing lifecycle lease also covers this second deletion.
            _services._delete_media_files([_services._account_video_ok_path(path)], allowed_roots=(work_dir,),
                                protection=protection)
            total -= size
            removed += 1
            freed += size
    return removed, freed

REQUIRES = ('Path', '_NATIVE_CACHE_VERSION', '_account_video_ok_path', '_cleanup_rejected_prepare', '_cleanup_uploaded_item', '_delete_media_files', '_expected_prefetch_paths', '_lock_for_account_video', '_media_companions', '_native_cache_fingerprint', '_within', 'config', 'holoassist', 'journal_delivery_confirmed', 'json', 'os', 're')
