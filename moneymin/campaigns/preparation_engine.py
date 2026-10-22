"""Video preparation, source validation and cache ownership for campaigns."""
from __future__ import annotations

def _ffmpeg_head(_services, ff: str) -> list[str]:
    """Flags que evitam o ffmpeg pausar no stdin ou abrir console no Windows."""
    return [ff, "-hide_banner", "-nostdin", "-y", "-v", "error"]

def _ffmpeg_run(_services, cmd: list[str], timeout: int = 3600):
    import subprocess
    kwargs: dict[str, Any] = {
        "capture_output": True, "text": True, "timeout": timeout,
    }
    if _services.os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, **kwargs)

def _heavy_encode_threads(_services) -> int:
    """Todos os núcleos: o PUT só começa depois do prepare."""
    return max(1, _services.os.cpu_count() or 4)

def _ffmpeg_encoder_available(_services, ff: str, encoder: str) -> bool:
    """Confirma o encoder e, para NVENC, testa o hardware uma vez por processo."""
    import subprocess
    try:
        res = _services._ffmpeg_run([ff, "-hide_banner", "-encoders"], timeout=20)
    except (OSError, TimeoutError, subprocess.TimeoutExpired):
        return False
    output = f"{getattr(res, 'stdout', '')}\n{getattr(res, 'stderr', '')}"
    if res.returncode != 0 or encoder not in output:
        return False
    if encoder == "h264_nvenc":
        # Builds Windows anunciam NVENC mesmo em PCs sem placa NVIDIA/driver.
        # Valide um frame com a configuração real antes de preparar mídia longa.
        try:
            probe = _services._ffmpeg_run([
                *_services._ffmpeg_head(ff), "-f", "lavfi", "-i",
                "color=c=black:s=1440x1080:r=30", "-frames:v", "1", "-an",
                *_services._native_video_codec_args(nvenc=True), "-f", "null", "-",
            ], timeout=20)
        except (OSError, TimeoutError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0
    return True

def _use_nvenc(_services, ff: str) -> bool:
    """Seleciona RTX/NVENC automaticamente; MINUTE_VIDEO_ENCODER força CPU/GPU."""
    requested = _services.os.environ.get("MINUTE_VIDEO_ENCODER", "auto").strip().lower()
    if requested in {"cpu", "x264", "libx264", "off", "disabled"}:
        return False
    return _services._ffmpeg_encoder_available(ff, "h264_nvenc")

def _native_video_codec_args(_services, 
    *,
    nvenc: bool,
    bitrate: str | None = None,
    gop: int | None = None,
) -> list[str]:
    """H.264 High@4.2 sem B-frames — MediaRecorder do app (profile 8, level 8192).

    O lado do metadata (codecActuals) reporta hasBFrames/gopMaxFrames null,
    exatamente como o EgoCodecActuals do app. GOP padrão de 30 (= ~1
    keyframe/s, como o MediaRecorder) em QUALQUER encode — o app não deixa o
    GOP do encoder crescer para ~8 s.
    """
    br = bitrate or _services.NATIVE_BITRATE
    # GOP ~1s (30 a 30fps): o app grava ~1 keyframe/s e o frames.csv espelha
    # os keyframes reais do MP4. Default 30; o re-encode por aparelho usa o GOP
    # do perfil (28-32) via `gop`.
    gop_value = int(gop) if gop is not None else 30
    gop_args = ["-g", str(gop_value), "-keyint_min", str(gop_value)]
    if nvenc:
        return [
            "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq",
            "-rc", "vbr", "-b:v", br, "-maxrate", br, "-bufsize", "16000k",
            "-profile:v", "high", "-level:v", "4.2",
            "-spatial-aq", "1", "-temporal-aq", "1",
            "-rc-lookahead", "20", "-bf", "0",
            *gop_args,
        ]
    return [
        "-c:v", "libx264", "-preset", "veryfast",
        "-threads", str(_services._heavy_encode_threads()),
        "-b:v", br, "-maxrate", br, "-bufsize", "16000k",
        "-profile:v", "high", "-level", "4.2",
        "-bf", "0",
        *gop_args,
    ]

def _native_container_args(_services, tmp: Path) -> list[str]:
    """Envelope Android: 1440x1080 yuv420p TV, VideoHandler/SoundHandler,
    AAC 48 kHz ESTÉREO 256 kbps (EgoAudioConfig 2ch/48000/256000)."""
    return [
        "-vf", ("scale=1440:1080:force_original_aspect_ratio=increase,"
                "crop=1440:1080,scale=in_range=full:out_range=tv,format=yuv420p"),
        "-r", "30",
        "-color_range", "tv", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-metadata:s:v:0", "handler_name=VideoHandler",
        "-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "256k",
        "-metadata:s:a:0", "handler_name=SoundHandler",
        "-movflags", "+faststart", "-f", "mp4", str(tmp),
    ]

def _measured_pts_container_args(_services, tmp: Path) -> list[str]:
    """Keep the existing container envelope without converting measured VFR."""
    args = _services._native_container_args(tmp)
    rate = args.index("-r")
    del args[rate:rate + 2]
    return [*args[:-1], "-fps_mode", "passthrough", "-enc_time_base", "1:1000000",
            "-video_track_timescale", "1000000", args[-1]]

def _measured_video_pts(_services, path: Path) -> list[int]:
    from ..sidecar import _extract_frame_pts
    frames = _extract_frame_pts(path)
    if not frames or any(b[0] <= a[0] for a, b in zip(frames, frames[1:])):
        raise _services.UploadError("Vídeo sem PTS medidos válidos.", transient=False, phase="prepare")
    return [frame[0] - frames[0][0] for frame in frames]

def _require_same_video_pts(_services, source: Path, output: Path, *, start_ns: int = 0,
                            end_ns: int | None = None) -> int:
    original = [ts for ts in _services._measured_video_pts(source)
                if ts >= start_ns and (end_ns is None or ts < end_ns)]
    measured = _services._measured_video_pts(output)
    if (not original or len(original) != len(measured)
            or any(abs((ts - original[0]) - actual) > 1000
                   for ts, actual in zip(original, measured))):
        raise _services.UploadError("PTS alterados durante o recorte ou encode Nymeria.",
                          transient=False, phase="prepare")
    return original[0]

def _measured_packet_tail_args(_services, source: Path, *, start_ns: int = 0,
                               end_ns: int | None = None) -> list[str]:
    pts = [ts for ts in _services._measured_video_pts(source)
           if ts >= start_ns and (end_ns is None or ts < end_ns)]
    source_end = int(_services.probe_video(source).get("duration_ms") or 0) * 1_000_000
    end = source_end if end_ns is None else min(source_end, end_ns)
    if not pts or end <= pts[-1]:
        raise _services.UploadError("Duração medida do último frame Nymeria inválida.",
                          transient=False, phase="prepare")
    tail_us = round((end - pts[-1]) / 1000)
    return ["-bsf:v", f"setts=duration=if(eq(N\\,{len(pts) - 1})\\,{tail_us}\\,DURATION)"]

def _cpu_slots(_services, n: int) -> Iterator[None]:
    """Reserva `n` slots do semáforo de encode (normalize pega todos)."""
    n = max(1, min(int(n), _services.ACCOUNT_ENCODE_WORKERS))
    acquired = 0
    try:
        with _services._ACCOUNT_ENCODE_RESERVATION_LOCK:
            for _ in range(n):
                _services._ACCOUNT_ENCODE_SLOTS.acquire()
                acquired += 1
        yield
    finally:
        for _ in range(acquired):
            _services._ACCOUNT_ENCODE_SLOTS.release()

def _normalize_video(_services, src: Path, out_dir: Path, *,
                    start_s: float | None = None,
                    dur_s: float | None = None,
                    stem: str | None = None) -> Path:
    """Prepare a bound cache without destroying the prior pair on failure.

    Source windows and encoder parameters are unchanged. Publication is
    serialized for cooperating threads; two-file crash atomicity and external
    writers are not guaranteed. Legacy v4 caches migrate only after success.
    """
    ff = _services._ffmpeg_bin()
    out = out_dir / f"{stem or src.stem}_native.mp4"
    marker = out.with_name(out.name + ".source.json")
    with _services._native_cache_guard(out):
        try:
            cache_key = _services._native_cache_key(src, start_s, dur_s)
        except OSError as exc:
            raise RuntimeError(f"fonte Ego4D inacessível: {src}: {exc}") from exc
        if out.exists():
            try:
                saved = _services.json.loads(marker.read_text(encoding="utf-8"))
                if (_services._native_cache_marker_matches(saved, src, out, start_s, dur_s)
                        and _services._native_cache_duration_ok(_services.probe_video(out), dur_s)
                        and _services._native_cache_marker_matches(saved, src, out, start_s, dur_s)):
                    _services._record_generated_media(out, root=out_dir, role="prepared_video")
                    return out
            except (OSError, ValueError, TypeError, RuntimeError):
                pass
        tmp = marker_tmp = None
        backups: dict[Path, Path | None] = {}
        published = False
        restoration_failed = False
        try:
            cmd = [*_services._ffmpeg_head(ff)]
            # Seek híbrido: -ss no input (rápido) + -ss no output (alinha ao IMU).
            # Só -ss antes de -i pega keyframe anterior e o catbear vê vídeo ≠ sensor.
            if start_s is not None and float(start_s) > 0.05:
                pre = max(0.0, float(start_s) - 2.0)
                skip = float(start_s) - pre
                cmd += ["-ss", f"{pre:.3f}", "-i", str(src), "-ss", f"{skip:.3f}"]
            else:
                cmd += ["-i", str(src)]
                if start_s is not None and float(start_s) > 0:
                    cmd += ["-ss", f"{float(start_s):.3f}"]
            if dur_s is not None:
                cmd += ["-t", f"{float(dur_s):.3f}"]
            tmp = _services._native_cache_temp(out, ".tmp.mp4")
            input_args = cmd
            common_args = _services._native_container_args(tmp)
            use_nvenc = _services._use_nvenc(ff)
            cmd = [*input_args, *_services._native_video_codec_args(nvenc=use_nvenc), *common_args]
            slots = min(2, _services.ACCOUNT_ENCODE_WORKERS) if use_nvenc else _services.ACCOUNT_ENCODE_WORKERS
            with _services._cpu_slots(slots):
                res = _services._ffmpeg_run(cmd)
            if res.returncode != 0 and use_nvenc:
                tmp.unlink(missing_ok=True)
                cmd = [*input_args, *_services._native_video_codec_args(nvenc=False), *common_args]
                with _services._cpu_slots(_services.ACCOUNT_ENCODE_WORKERS):
                    res = _services._ffmpeg_run(cmd)
            if res.returncode != 0:
                raise RuntimeError(f"falha ao normalizar vídeo: {res.stderr.strip()[:400]}")
            if not _services._native_cache_duration_ok(_services.probe_video(tmp), dur_s):
                raise RuntimeError("normalize gerou vídeo com duração inválida")
            prepared_size, prepared_hash = _services._native_cache_fingerprint(tmp)
            if _services._native_cache_key(src, start_s, dur_s) != cache_key:
                raise RuntimeError("fonte alterada durante normalização")
            saved = {**cache_key, "prepared_size": prepared_size,
                     "prepared_sha256": prepared_hash}
            serialized = _services.json.dumps(saved, sort_keys=True, separators=(",", ":"))
            marker_tmp = _services._native_cache_temp(marker, ".tmp")
            marker_tmp.write_text(serialized, encoding="utf-8")
            if _services.json.loads(marker_tmp.read_text(encoding="utf-8")) != saved:
                raise RuntimeError("marcador de cache não corresponde ao candidato")
            if (_services._native_cache_key(src, start_s, dur_s) != cache_key
                    or _services._native_cache_fingerprint(tmp) != (prepared_size, prepared_hash)):
                raise RuntimeError("fonte ou candidato alterado antes de publicação")
            # Hardlinks preserve exact old bytes without copying a large MP4.
            # Failure creating either backup happens before any publication.
            for path in (out, marker):
                backups[path] = _services._native_cache_backup(path)
            if _services._native_cache_key(src, start_s, dur_s) != cache_key:
                raise RuntimeError("fonte alterada antes de publicação")
            tmp.replace(out)
            published = True
            marker_tmp.replace(marker)
            _services._record_generated_media(out, root=out_dir, role="prepared_video")
            return out
        except BaseException as exc:
            if published:
                try:
                    for path in (out, marker):
                        backup = backups[path]
                        if backup is None:
                            path.unlink(missing_ok=True)
                        elif (not path.exists()
                              or _services._native_cache_fingerprint(path, allow_empty=True)
                              != _services._native_cache_fingerprint(backup, allow_empty=True)):
                            _services.os.replace(backup, path)
                except (OSError, RuntimeError):
                    restoration_failed = True
                    exc.add_note("cache rollback incomplete; rollback files retained")
            raise
        finally:
            for path in (tmp, marker_tmp):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            if not restoration_failed:
                for backup in backups.values():
                    if backup is not None:
                        try:
                            backup.unlink(missing_ok=True)
                        except OSError:
                            pass

def _ffmpeg_bin(_services) -> str:
    return _services.ffmpeg_bin()

def _ego_clip_inputs(_services, 
    clip_info: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Preserva a janela precisa do ranking; UID é só identidade, não storage."""
    parent = str(clip_info.get("parent_video_uid") or "")
    window = clip_info.get("window_s")
    s3_path = str(clip_info.get("s3_path") or "")
    if parent and window and len(window) == 2 and s3_path:
        video = _services.ego4d._cat().videos.get(parent)
        if video is not None:
            start, end = float(window[0]), float(window[1])
            row = dict(clip_info)
            row.update({
                "exported_clip_uid": str(
                    clip_info.get("exported_clip_uid")
                    or clip_info.get("clip_uid") or ""),
                "parent_video_uid": parent,
                "parent_start_sec": str(start),
                "parent_end_sec": str(end),
                "s3_path": s3_path,
            })
            return row, video
    clip, video = _services.ego4d.find_clip(str(clip_info.get("clip_uid") or ""))
    if clip is not None and clip_info.get("selection_evidence") is not None:
        clip = dict(clip)
        clip["selection_evidence"] = clip_info["selection_evidence"]
    return clip, video

def _native_cache_guard(_services, native: Path) -> Iterator[None]:
    """Serialize cooperating threads, including reclaim read/match/delete.

    This is a process-local guard, not a cross-process lock or a crash journal.
    """
    key = _services.os.path.normcase(str(_services.Path(native).resolve()))
    with _services._NATIVE_CACHE_LOCKS_GUARD:
        lock = _services._NATIVE_CACHE_LOCKS.setdefault(key, _services.threading.RLock())
    with lock:
        yield

def _native_cache_fingerprint(_services, path: Path, *, allow_empty: bool = False) -> tuple[int, str]:
    """Hash actual bytes, rejecting observed replacement/change while reading."""
    def identity(info: Any) -> tuple[int, int, int, int]:
        # Windows fstat/stat ctime semantics differ; compare file ID and mtime.
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    digest = _services.hashlib.sha256()
    size = 0
    with _services.Path(path).open("rb") as handle:
        before = _services.os.fstat(handle.fileno())
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
        after = _services.os.fstat(handle.fileno())
    if ((size == 0 and not allow_empty) or size != before.st_size or identity(before) != identity(after)
            or identity(after) != identity(_services.Path(path).stat())):
        raise RuntimeError("arquivo de cache vazio ou alterado durante leitura")
    return size, digest.hexdigest()

def _record_generated_media(_services, path: Path, *, root: Path, role: str) -> None:
    """Bind a successfully produced derivative to its actual bytes."""
    from ..media_lifecycle import record_managed_media
    _size, digest = _services._native_cache_fingerprint(path)
    provider = ("nymeria" if path.name.startswith("nymeria_") else
                "holoassist" if path.name.startswith("holoassist_") else "ego4d")
    record_managed_media(path, root=root, provider=provider, role=role,
                         expected_digest=digest)

def _native_cache_key(_services, 
    src: Path, start_s: float | None, dur_s: float | None,
) -> dict[str, Any]:
    """Same source bytes and unchanged source-window/encoder identity."""
    source_stat = src.stat()
    size, digest = _services._native_cache_fingerprint(src)
    if source_stat.st_size != size or source_stat.st_mtime_ns != src.stat().st_mtime_ns:
        raise RuntimeError("fonte alterada durante identificação")
    return {
        "version": _services._NATIVE_CACHE_VERSION,
        "source_size": size,
        "source_mtime_ns": source_stat.st_mtime_ns,
        "source_sha256": digest,
        "start_s": None if start_s is None else round(float(start_s), 6),
        "dur_s": None if dur_s is None else round(float(dur_s), 6),
        "width": 1440,
        "height": 1080,
        "fps": 30,
    }

def _native_cache_marker(_services, 
    src: Path, native: Path, start_s: float | None, dur_s: float | None,
) -> dict[str, Any]:
    """Describe current bytes explicitly; does not establish media provenance."""
    with _services._native_cache_guard(native):
        key = _services._native_cache_key(src, start_s, dur_s)
        size, digest = _services._native_cache_fingerprint(native)
        if _services._native_cache_key(src, start_s, dur_s) != key:
            raise RuntimeError("fonte alterada durante validação de cache")
        return {**key, "prepared_size": size, "prepared_sha256": digest}

def _native_cache_marker_matches(_services, 
    saved: Any, src: Path, native: Path,
    start_s: float | None, dur_s: float | None,
) -> bool:
    """Fail closed on v4, malformed markers or changed source/output bytes."""
    if (type(saved) is not dict or type(saved.get("version")) is not int
            or saved["version"] != _services._NATIVE_CACHE_VERSION):
        return False
    try:
        expected = _services._native_cache_marker(src, native, start_s, dur_s)
        return (type(saved) is dict and saved == expected
                and all(type(saved[key]) is type(value) for key, value in expected.items()))
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        return False

def _native_cache_duration_ok(_services, probe: dict[str, Any], dur_s: float | None) -> bool:
    value = probe.get("duration_ms")
    if type(value) not in (int, float) or not _services.math.isfinite(value) or value <= 0:
        return False
    return dur_s is None or abs(value - round(float(dur_s) * 1000)) <= 1000

def _native_cache_temp(_services, path: Path, suffix: str) -> Path:
    candidate = path.with_name(path.name + "." + _services.uuid.uuid4().hex + suffix)
    # Exclusive creation: never delete a temporary path belonging to a peer.
    with candidate.open("xb"):
        pass
    return candidate

def _native_cache_backup(_services, path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(path.name + "." + _services.uuid.uuid4().hex + ".rollback")
    _services.os.link(path, backup)
    return backup

def ego_clip_cache_state(_services, 
    clip_info: dict[str, Any], work_dir: Path,
) -> str:
    """`ready`, `partial`, `pending` ou `unresolved` para o reservatório Ego4D.

    `ready` exige a mesma fonte, o mesmo marcador de encode e o IMU que
    `prepare_clip` reaproveita. Não sonda o MP4: a campanha ainda valida a
    duração quando for usar o arquivo.
    """
    try:
        row, video = _services._ego_clip_inputs(clip_info)
        if row is None or video is None:
            return "unresolved"
        plan = _services._ego_prepare_plan(row)
    except (OSError, ValueError, TypeError, KeyError, _services.json.JSONDecodeError):
        return "unresolved"
    work = _services.Path(work_dir)
    source = work / plan["source_name"]
    native = work / plan["native_name"]
    imu = work / plan["imu_name"]
    try:
        source_ok = source.is_file() and source.stat().st_size > 1024 * 1024
        imu_ok = _services.ego4d._valid_imu_cache(imu)
        native_ok = False
        if source_ok and native.is_file() and native.stat().st_size > 1024 * 1024:
            marker = native.with_name(native.name + ".source.json")
            saved = _services.json.loads(marker.read_text(encoding="utf-8"))
            native_ok = _services._native_cache_marker_matches(
                saved, source, native, plan["norm_start"], plan["dur_s"])
    except (OSError, ValueError, TypeError, _services.json.JSONDecodeError):
        source_ok = imu_ok = native_ok = False
    try:
        touched = source.exists() or native.exists() or imu.exists()
    except OSError:
        touched = False
    if source_ok and imu_ok and native_ok:
        return "ready"
    if touched:
        return "partial"
    return "pending"

def _try_prepare_imu_carve(_services, 
    clip_info: dict[str, Any],
    work_dir: Path,
    *,
    min_dur_s: float,
    max_dur_s: float,
    allow_download: bool,
    progress: Callable[[str, dict[str, Any]], None] | None,
    log: Callable[[str], None],
    emit: Callable[..., None],
    display_name: str,
) -> dict[str, Any] | None:
    """On IMU-gap prepare failure, retry the longest continuous subwindow."""
    from ..imu_coverage import carve_continuous_window

    carved = carve_continuous_window(
        clip_info, work_dir, min_s=min_dur_s, max_s=max_dur_s)
    if carved is None:
        return None
    log(f"    [i] IMU com lacuna — tentando subjanela contínua "
        f"{carved.get('clip_uid')}")
    emit("clip_imu_carve", clip_uid=clip_info.get("clip_uid"),
         carved_uid=carved.get("clip_uid"), task_name=display_name)
    parent = carved.get("parent_video_uid")
    video = _services.ego4d._cat().videos.get(parent) if parent else None
    if video is None:
        return None
    try:
        item = _services.prepare_clip(
            carved, video, work_dir, progress=progress,
            allow_download=allow_download)
    except Exception:  # noqa: BLE001
        return None
    return {"item": item, "clip": carved}

def prepare_clip(_services, 
    clip: dict[str, Any],
    video: dict[str, Any],
    work_dir: Path,
    *,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    allow_download: bool = True,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Baixa clipe + IMU real, normaliza o vídeo e monta o sidecar. (sem upload)"""
    from .. import content_provenance
    if should_stop is not None and should_stop():
        raise RuntimeError("preparo Ego4D interrompido pelo usuário; originais preservados")
    clip = _services._normalize_ego_clip_for_prepare(clip)
    # Cached media and legacy rows without a carrier never bypass task proof.
    selection_evidence = _services.ego4d.revalidate_selection_evidence(clip)
    work_dir = _services.Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    if not isinstance(clip.get("parent_video_uid"), str) or not clip["parent_video_uid"].strip():
        raise RuntimeError("Clipe Ego4D sem identidade do pai original")
    plan = _services._ego_prepare_plan(clip)
    clip_uid = plan["clip_uid"]
    window_s = plan["window_s"]
    parent_uid = plan["parent_uid"]
    if video.get("video_uid") and video["video_uid"] != parent_uid:
        raise RuntimeError("Metadados do vídeo não correspondem ao pai original do clipe Ego4D")
    dur_s = plan["dur_s"]
    dur_ms = int(round(dur_s * 1000))
    if not (_services.MIN_DUR_MS <= dur_ms <= _services.MAX_DUR_MS):
        raise RuntimeError(f"clipe {clip_uid} fora da janela de duração ({dur_ms}ms)")
    if not _services.ego4d.imu_window_is_covered(video, window_s):
        raise RuntimeError(
            f"clipe {clip_uid} atravessa trecho sem cobertura contínua de IMU")

    def _progress(phase: str, **payload: Any) -> None:
        if should_stop is not None and should_stop():
            raise RuntimeError("preparo Ego4D interrompido pelo usuário; originais preservados")
        if progress:
            progress(phase, payload)

    # Valide o sensor ANTES de baixar/codificar gigabytes de vídeo. O catálogo
    # informa a cobertura geral, mas alguns aparelhos têm lacunas finas (por
    # exemplo, acelerômetro ausente por ~1 s) visíveis somente no CSV. Antes a
    # campanha gastava vários minutos no encode e só então descartava o clipe.
    _progress("imu_lookup")
    local_imu = work_dir / f"{parent_uid}_imu.csv"
    if not allow_download and not _services.ego4d._valid_imu_cache(local_imu):
        raise RuntimeError(f"IMU local ausente ou inválida para {clip_uid}")
    imu_path = _services.ego4d.download_imu(video, local_imu) if allow_download else local_imu
    if imu_path is None:
        raise RuntimeError(
            f"clipe {clip_uid} sem IMU real — não enviar (coerência sensor falharia)."
            f" Escolha um clipe com has_imu=true.")
    original_imu_binding = content_provenance.fingerprint(imu_path)
    _progress("imu_preflight")
    _services.ego4d.build_imu_csv(
        imu_path, window_s, duration_ms=dur_ms, validate_only=True)
    _progress("imu_ready")

    _progress("video_lookup")
    source_video_path = work_dir / plan["source_name"]
    if not allow_download and not _services.ego4d._valid_mp4_cache(source_video_path):
        raise RuntimeError(f"vídeo local ausente ou inválido para {clip_uid}")
    if plan["needs_cut"]:
        # Prefere o clip oficial CRF 18 quando ele contém a janela; o IMU
        # continua no relógio absoluto do vídeo canônico.
        if allow_download and not _services.ego4d._valid_mp4_cache(source_video_path):
            source = dict(clip)
            source["needs_cut"] = False
            _services.ego4d.download_clip(source, source_video_path)
    elif allow_download:
        _services.ego4d.download_clip(clip, source_video_path)
    original_video_binding = content_provenance.fingerprint(source_video_path)
    _progress("encode")
    native = _services._normalize_video(
        source_video_path, work_dir,
        start_s=plan["norm_start"], dur_s=dur_s, stem=clip_uid)
    _progress("video_ready", bytes=native.stat().st_size)
    probe = _services.probe_video(native)
    if not probe.get("duration_ms"):
        raise RuntimeError(
            f"clipe {clip_uid}: native sem duração após normalize "
            f"({native})")

    # Duração do ARQUIVO (probe) — o encoder nunca entrega N.000 s.
    dur_ms = int(probe["duration_ms"])
    fps = float(probe.get("fps") or 30.0)
    tolerance_ms = max(50, _services.math.ceil(2000 / fps)) if _services.math.isfinite(fps) and fps > 0 else 100
    if abs(dur_ms - round(dur_s * 1000)) > tolerance_ms:
        raise RuntimeError(
            f"clipe {clip_uid}: duração codificada divergente da janela original "
            f"({dur_ms}ms versus {round(dur_s * 1000)}ms)")
    _progress("sidecar")
    imu_diag: dict[str, Any] = {}
    imu_csv = _services.ego4d.build_imu_csv(
        imu_path, window_s, duration_ms=dur_ms, stats=imu_diag)
    # 500 Hz (EgoImu.SAMPLING_PERIOD_US=2000) — o n_samples alimenta o
    # imuDiagnostics.sampleCount e precisa bater com as linhas do CSV.
    n_samples = max(
        1, int(dur_ms / 1000 * _services.config.ANDROID_IMU_SAMPLE_RATE_HZ) + 1)
    frames_csv = _services._frames_csv(dur_ms, fps=(probe.get("fps") or 30.0))
    content_provenance.verify_input(original_imu_binding)
    content_provenance.verify_input(original_video_binding)
    lineage = content_provenance.prepare_content_provenance(
        source_video_path, imu_path, native, imu_csv, frames_csv,
        clip_uid=clip_uid, parent_video_uid=parent_uid,
        media_uid=str(clip.get("media_uid") or clip_uid), window_s=window_s,
        # The raw exported clip may already begin at a nonzero canonical
        # time. Record the actual declared source→window mapping used here.
        media_offset_s=window_s[0] - float(plan["norm_start"] or 0.0),
        normalization_start_s=plan["norm_start"], selection_evidence=selection_evidence)
    n_samples = lineage["derived_diagnostics"]["output"]["sample_count"]

    return {
        **lineage,
        # Runtime-only selection carrier keeps source location for fresh
        # validation. History serialization excludes this private structure.
        "_content_candidate": dict(clip),
        "task_name_authoritative": (selection_evidence or {}).get("task", {}).get("name"),
        "clip_uid": clip_uid,
        "video_path": str(native),
        "duration_ms": dur_ms,
        "device": video.get("device"),
        "scenario": " | ".join(_services.ego4d.scenario_values(video)),
        "imu_real": bool(imu_path),
        # Preserve source provenance for duration/sensor checks.
        "imu_csv": imu_csv, "frames_csv": frames_csv,
        "imu_path": str(imu_path),
        "window_s": list(window_s),
        "n_samples": n_samples, "probe": probe,
        # Contadores do resample → imuDiagnostics forjado no zip (Minute 1.29).
        "imu_diagnostics": dict(imu_diag),
        "source": "ego4d",
        # Local audit/history only. Never relabel third-party footage as a
        # newly captured recording or claim the output grid as the native rate.
        "source_provenance": {
            **lineage["content_provenance"],
            "dataset": "ego4d", "recording_origin": "third_party_dataset",
            "parent_video_uid": parent_uid, "clip_uid": clip_uid,
            "parent_identity_verified": video.get("video_uid") == parent_uid,
            "window_s": list(window_s), "original_device": video.get("device"),
            "prepared_duration_ms": dur_ms,
            "imu": {
                "source_file": _services.Path(imu_path).name,
                "processing": "resampled_measured_signals",
                "output_grid_hz": _services.config.ANDROID_IMU_SAMPLE_RATE_HZ,
                "native_rate_hz": None,
            },
        },
        # Exclusivamente interno: nunca entra no log. Só é consumido após todos
        # os uploads deste item confirmarem sucesso.
        "_cleanup_paths": [
            str(source_video_path), str(native), str(imu_path),
        ],
    }

def prepare_nymeria_clip(_services, 
    clip: dict[str, Any],
    work_dir: Path,
    *,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    allow_download: bool = True,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Extrai RGB+IMU reais do Aria VRS e monta item Minute-ready."""
    from .. import content_provenance, nymeria_vrs, nymeria_library

    if should_stop is not None and should_stop():
        raise RuntimeError("preparo Nymeria interrompido pelo usuário; originais preservados")
    work_dir = _services.Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    def _progress(phase: str, **payload: Any) -> None:
        if should_stop is not None and should_stop():
            raise RuntimeError("preparo Nymeria interrompido pelo usuário; originais preservados")
        if progress:
            progress(phase, payload)

    if clip.get("acquisition_required") is True:
        if not allow_download:
            raise RuntimeError("Fonte Nymeria ainda não adquirida; modo somente cache impede download.")
        _services.nymeria.revalidate_planned_candidate(clip)
        _progress("source_acquisition")
        def _acquisition_progress(value: Any) -> None:
            payload = value if isinstance(value, dict) else {"message": str(value)}
            # The library's public callback also serves string-only consumers.
            # Translate its byte counter at this campaign adapter boundary.
            import re
            byte_count = re.search(r": (\d+)/(\d+) bytes$", str(payload.get("message", "")))
            if byte_count:
                current, total = map(int, byte_count.groups())
                if 0 <= current <= total and total > 0:
                    payload = {**payload, "current": current, "total": total}
            _progress("source_acquisition", **payload)
        nymeria_library.ensure_sequence_for_campaign(
            clip, progress=_acquisition_progress, should_stop=should_stop)
        clip = _services.nymeria.resolve_planned_candidate(clip)
        _progress("source_measured", measured=True)

    clip_uid = str(clip.get("clip_uid") or "")
    seq_dir = _services.Path(str(clip.get("path") or ""))
    window = clip.get("window_s")
    if not clip_uid.startswith("nymeria:") or not seq_dir.is_dir():
        raise RuntimeError("identidade Nymeria inválida")
    if not window or len(window) != 2:
        raise RuntimeError("janela Nymeria inválida")
    start_s, end_s = float(window[0]), float(window[1])
    if not (_services.math.isfinite(start_s) and _services.math.isfinite(end_s) and end_s > start_s):
        raise RuntimeError("janela Nymeria inválida")
    dur_s = end_s - start_s
    dur_ms = int(round(dur_s * 1000))
    if not (_services.MIN_DUR_MS <= dur_ms <= _services.MAX_DUR_MS):
        raise RuntimeError(f"clipe {clip_uid} fora da janela de duração ({dur_ms}ms)")

    motion_vrs = seq_dir / "recording_head" / "data" / "motion.vrs"
    data_vrs = seq_dir / "recording_head" / "data" / "data.vrs"
    if not motion_vrs.is_file() or not data_vrs.is_file():
        raise RuntimeError(f"VRS Nymeria ausente em {seq_dir}")

    # Fresh action/window/source proof is required before any heavy decode.
    clip = _services.nymeria.revalidate_candidate(clip)
    inputs = {"source_video": content_provenance.fingerprint(data_vrs),
              "source_imu": content_provenance.fingerprint(motion_vrs)}
    _progress("imu_lookup")
    t0_ns, t1_ns = _services.nymeria.device_window_for_sequence(
        seq_dir, start_s=start_s, end_s=end_s)
    _progress("imu_preflight")
    samples = nymeria_vrs.read_imu_samples(
        motion_vrs, t0_ns=t0_ns, t1_ns=t1_ns, label="imu-right",
        stats=(read_stats := {}))
    imu_diag: dict[str, Any] = {}
    # Validate continuity before expensive RGB extract
    _ = nymeria_vrs.build_imu_csv_from_samples(
        samples, t0_ns=t0_ns, duration_ms=int(round((t1_ns - t0_ns) / 1e6)))
    _progress("imu_ready", samples=len(samples))

    stem = "nymeria_" + clip_uid.replace(":", "_").replace(".", "_")
    native = work_dir / f"{stem}_native.mp4"
    rgb_diag: dict[str, Any] = {}
    _progress("video_lookup")
    # Encode once from measured VRS frames. The generic normalizer forces
    # CFR30, so this path retains its envelope/codec and removes that rate.
    container_args = _services._native_container_args(native)[:-1]
    rate_index = container_args.index("-r")
    del container_args[rate_index:rate_index + 2]
    with _services._cpu_slots(_services.ACCOUNT_ENCODE_WORKERS):
        nymeria_vrs.extract_rgb_mp4(
            data_vrs, native, t0_ns=t0_ns, t1_ns=t1_ns,
            output_args=[*_services._native_video_codec_args(nvenc=False), *container_args],
            stats=rgb_diag, should_stop=should_stop, progress=_progress)
    _services._record_generated_media(native, root=work_dir, role="prepared_video")
    _progress("video_ready", bytes=native.stat().st_size)
    _progress("encode_ready", encoder="CPU", measured_pts=True)
    probe = _services.probe_video(native)
    dur_ms = int(probe.get("duration_ms") or 0)
    if not (_services.MIN_DUR_MS <= dur_ms <= _services.MAX_DUR_MS):
        raise RuntimeError(
            f"clipe {clip_uid} fora da janela de duração ({dur_ms}ms)")
    _progress("sidecar")
    imu_csv = nymeria_vrs.build_imu_csv_from_samples(
        samples, t0_ns=int(rgb_diag["firstCaptureTimestampNs"]),
        duration_ms=dur_ms,
        source_dropped_timestamps_ns=read_stats["droppedRowTimestampsNs"],
        stats=imu_diag)
    n_samples = int(imu_diag["sampleCount"])
    frames_csv = _services.build_frames_csv_from_video(
        native, duration_ms=dur_ms, require_measured_pts=True)
    for binding in inputs.values():
        content_provenance.verify_input(binding)
    inputs["prepared_video"] = content_provenance.fingerprint(native)
    diagnostics = {
        "schema": 1, "kind": "local_derived_vrs_observation",
        "source_clock": "aria_DEVICE_TIME_ns", "output_clock": "local_relative_csv_ns",
        "physical_provenance_verified": False, "native_equivalence_verified": False,
        "rgb": dict(rgb_diag), "imu": dict(imu_diag),
        "output": {"sample_count": n_samples,
                   "uniform_grid_step_ns": int(round(1e9 / _services.config.ANDROID_IMU_SAMPLE_RATE_HZ)),
                   **content_provenance.text_binding(imu_csv)},
    }
    lineage = {
        "schema": 1, "dataset": "nymeria", "recording_origin": "third_party_dataset",
        "physical_provenance_verified": False, "receiver_derived_data_support_verified": False,
        "clip_uid": clip_uid, "parent_video_uid": clip.get("seq_id"),
        "media_uid": clip.get("seq_id"), "window_s": [start_s, end_s],
        "transform": {"kind": "measured_vrs_window_and_vfr_encoding",
                      "presentation_rotation_degrees_clockwise": 90,
                      "source_device_window_ns": [t0_ns, t1_ns],
                      "first_rgb_capture_ns": rgb_diag["firstCaptureTimestampNs"],
                      "imu": "measured_device_time_linear_resampling",
                      "frames_at_prepare": "file_extracted_measured_pts"},
        "assets": {**{role: content_provenance.public_binding(row)
                       for role, row in inputs.items()},
                   "imu_csv": content_provenance.text_binding(imu_csv),
                   "prepared_frames_csv": content_provenance.text_binding(frames_csv)},
        "selection_evidence": clip.get("selection_evidence"),
        "derived_diagnostics": diagnostics,
    }
    lineage["lineage_sha256"] = content_provenance.canonical_digest(lineage)
    return {
        "clip_uid": clip_uid,
        "video_path": str(native),
        "duration_ms": dur_ms,
        "device": "Project Aria",
        "scenario": str(clip.get("scenario") or clip.get("script") or ""),
        "imu_real": True,
        "imu_csv": imu_csv,
        "frames_csv": frames_csv,
        "n_samples": n_samples,
        "imu_diagnostics": dict(imu_diag),
        "rgb_diagnostics": dict(rgb_diag),
        "selection_evidence": clip.get("selection_evidence"),
        "task_name_authoritative": clip.get("task_name_authoritative"),
        "task_id": clip.get("task_id"),
        "registry_key": clip.get("registry_key"),
        "content_provenance": lineage,
        "derived_diagnostics": diagnostics,
        "_content_inputs": inputs,
        "_content_candidate": dict(clip),
        "_nymeria_resample_inputs": {
            "motion_vrs": inputs["source_imu"], "data_vrs": inputs["source_video"],
            "source_device_window_ns": [t0_ns, t1_ns],
            "origin_ns": int(rgb_diag["firstCaptureTimestampNs"]),
            "duration_ms": dur_ms, "imu_label": "imu-right",
            "sample_rate_hz": _services.config.ANDROID_IMU_SAMPLE_RATE_HZ,
        },
        "probe": probe,
        "source": "nymeria",
        "window_s": [start_s, end_s],
        "seq_id": clip.get("seq_id"),
        "source_provenance": {
            **lineage,
            "dataset": "nymeria",
            "recording_origin": "third_party_dataset",
            "seq_id": clip.get("seq_id"),
            "window_s": [start_s, end_s],
            "source_time_domain": "DEVICE_TIME",
            "first_rgb_capture_ns": rgb_diag["firstCaptureTimestampNs"],
            "source_device_window_ns": [t0_ns, t1_ns],
        },
        "_cleanup_paths": [str(native)],
    }

def prepare_holoassist_clip(_services, 
    clip: dict[str, Any],
    work_dir: Path,
    *,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    allow_download: bool = True,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Baixa e prepara uma gravação HoloAssist estritamente elegível.

    Usa o mesmo vídeo nativo, frames e sidecar do motor existente. O único
    adaptador específico converte o acelerômetro/giroscópio sincronizados do
    HoloLens para a grade de 500 Hz exigida pelo sidecar Minute Android.
    """
    if should_stop is not None and should_stop():
        raise RuntimeError("preparo HoloAssist interrompido pelo usuário; originais preservados")
    work_dir = _services.Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    clip_uid = str(clip.get("clip_uid") or "")
    video_name = str(clip.get("video_name") or clip.get("video_uid") or "")
    if not clip_uid.startswith("holoassist:") or not video_name:
        raise RuntimeError("identidade HoloAssist inválida")
    if str(clip.get("task_type") or "") not in _services.holoassist.FURNITURE_TASK_TYPES:
        raise RuntimeError("tarefa HoloAssist fora das categorias permitidas")
    if float(clip.get("correct_action_ratio") or 0) < _services.holoassist.MIN_CORRECT_ACTION_RATIO:
        raise RuntimeError("gravação HoloAssist abaixo do limiar de ações corretas")
    if clip.get("has_uncorrected_error"):
        raise RuntimeError("gravação HoloAssist contém erro não corrigido")

    def _progress(phase: str, **payload: Any) -> None:
        if should_stop is not None and should_stop():
            raise RuntimeError("preparo HoloAssist interrompido pelo usuário; originais preservados")
        if progress:
            progress(phase, payload)

    recording_dir = _services.holoassist.data_dir() / "recordings" / video_name
    pitchshift = recording_dir / "Video_pitchshift.mp4"
    compressed = recording_dir / "Video_compress.mp4"
    _progress("video_cached" if pitchshift.exists() or compressed.exists()
              else "video_lookup")

    def _download_holo_video(*, compressed: bool = False) -> Path:
        if progress:
            return _services.holoassist.download_video(
                video_name, compressed=compressed,
                progress=lambda phase, current, total: _progress(
                    phase, current=current, total=total
                ),
            )
        return _services.holoassist.download_video(video_name, compressed=compressed)

    if pitchshift.exists():
        source_video = pitchshift
    elif compressed.exists():
        source_video = compressed
    else:
        if not allow_download:
            raise RuntimeError(f"vídeo HoloAssist local ausente para {video_name}")
        try:
            source_video = _download_holo_video()
        except FileNotFoundError:
            # Algumas sessões anotadas não existem no TAR pitch-shifted oficial,
            # mas estão no TAR comprimido. É a mesma gravação e mantém o IMU.
            _progress("video_fallback", source="Video_compress.mp4")
            source_video = _download_holo_video(compressed=True)
    _progress("video_ready", bytes=source_video.stat().st_size)

    sensor_dir = _services.holoassist.data_dir() / "recordings" / video_name / "IMU"
    sensor_names = (
        "Accelerometer_sync.txt", "Gyroscope_sync.txt", "Magnetometer_sync.txt",
    )
    sensors_cached = all((sensor_dir / name).exists() for name in sensor_names)
    _progress("imu_cached" if sensors_cached else "imu_lookup")
    if not allow_download:
        sensors = {name: sensor_dir / name for name in sensor_names}
        if any(not path.is_file() or path.stat().st_size == 0
               for path in sensors.values()):
            raise RuntimeError(f"sensores HoloAssist locais ausentes para {video_name}")
    elif progress:
        sensors = _services.holoassist.download_imu(
            video_name,
            progress=lambda phase, current, total: _progress(
                phase, current=current, total=total
            ),
        )
    else:
        sensors = _services.holoassist.download_imu(video_name)
    _progress("imu_ready")
    safe_stem = "holoassist_" + video_name.replace("/", "_").replace("\\", "_")
    _progress("encode")
    native = _services._normalize_video(source_video, work_dir, stem=safe_stem)
    _progress(
        "encode_ready",
        encoder="NVENC" if _services._use_nvenc(_services._ffmpeg_bin()) else "CPU",
    )
    probe = _services.probe_video(native)
    dur_ms = int(probe.get("duration_ms") or 0)
    if not (_services.MIN_DUR_MS <= dur_ms <= _services.MAX_DUR_MS):
        raise RuntimeError(
            f"gravação {video_name} fora da janela de duração ({dur_ms}ms)"
        )
    _progress("sidecar")
    imu_csv = _services.holoassist.build_imu_csv(
        sensors["Accelerometer_sync.txt"],
        sensors["Gyroscope_sync.txt"],
        duration_ms=dur_ms,
    )
    frames_csv = _services._frames_csv(dur_ms, fps=(probe.get("fps") or 30.0))
    return {
        "clip_uid": clip_uid,
        "video_path": str(native),
        "duration_ms": dur_ms,
        "device": clip.get("device") or "Microsoft HoloLens 2",
        "scenario": str(clip.get("task_type") or clip.get("scenario") or ""),
        "imu_real": True,
        "imu_csv": imu_csv,
        "frames_csv": frames_csv,
        # 500 Hz (Android) — sampleCount do imuDiagnostics deve bater com o CSV.
        "n_samples": max(1, int(dur_ms / 1000 * _services.config.ANDROID_IMU_SAMPLE_RATE_HZ) + 1),
        "probe": probe,
        "source": "holoassist",
        "_cleanup_paths": [
            str(pitchshift), str(compressed), str(native),
            *(str(path) for path in sensors.values()),
        ],
    }

def _new_identity(_services, duration_s: float, email: str,
                  recorded_at: str | None = None,
                  *,
                  limits: dict[str, int] | None = None,
                  ) -> tuple[str, str, str]:
    """Gera session_id/log_id/recorded_at NOVOS (identidade única por conta).

    `recorded_at` explícito é validado na janela da política (sem reescrita).
    Sem valor pré-agendado, a gravação termina `gap` (1.5min–1.5h) antes do
    upload — nunca "agora".
    """
    session_id = str(_services.uuid.uuid4())
    backlog = _services.device_profile.effective_backlog_cap_ms(limits)
    if recorded_at:
        recorded_at = _services.device_profile.normalize_recorded_at(
            recorded_at, duration_s=duration_s, adjust=False,
            backlog_cap_ms=backlog)
        return session_id, f"{session_id}_0", recorded_at
    rng = _services.random.Random(f"{email}|{session_id}")
    gap_s = rng.uniform(90.0, 5400.0)
    start = _services.device_profile.recording_start_epoch(
        duration_s, gap_s=gap_s, backlog_cap_ms=backlog)
    recorded_at = _services.device_profile.format_recorded_at(start)
    return session_id, f"{session_id}_0", recorded_at

def _chunk_plan(_services, duration_ms: int, target_ms: int | None = None,
                limits: dict[str, int] | None = None,
                ) -> list[tuple[int, int]]:
    """Janelas (start_ms, dur_ms) respeitando os limites da política da sessão.

    Sem `limits`, cai nos defaults locais. O alvo padrão é o máximo da política:
    um vídeo dentro do intervalo vira um upload; só particiona acima do teto.
    """
    limits = dict(limits) if limits is not None else _services.config.recording_limits()
    eff_min = max(1_000, int(limits.get("min_duration_ms") or _services.MIN_DUR_MS))
    eff_max = max(eff_min, int(limits.get("max_duration_ms") or _services.MAX_DUR_MS))
    if target_ms is None:
        target_ms = eff_max
    target_ms = min(eff_max, max(eff_min, int(target_ms)))
    duration_ms = max(1, int(duration_ms))
    if duration_ms < eff_min:
        raise ValueError(
            f"duração {duration_ms} ms abaixo do mínimo remoto {eff_min} ms")
    if duration_ms <= target_ms:
        return [(0, duration_ms)]

    durations: list[int] = []
    remaining = duration_ms
    while remaining > target_ms:
        durations.append(target_ms)
        remaining -= target_ms
    durations.append(remaining)

    # Uma sobra menor que o mínimo não pode virar um chunk. Primeiro tenta
    # incorporá-la ao anterior sem ultrapassar o teto. Se isso não couber,
    # transfere duração dos chunks anteriores até a sobra alcançar o piso.
    if len(durations) > 1 and durations[-1] < eff_min:
        tail = durations.pop()
        if durations[-1] + tail <= eff_max:
            durations[-1] += tail
        else:
            needed = eff_min - tail
            for index in range(len(durations) - 1, -1, -1):
                transferable = max(0, durations[index] - eff_min)
                moved = min(needed, transferable)
                durations[index] -= moved
                tail += moved
                needed -= moved
                if needed == 0:
                    break
            if needed:
                raise ValueError(
                    f"duração {duration_ms} ms não pode ser dividida em chunks "
                    f"de {eff_min}–{eff_max} ms")
            durations.append(tail)

    if any(not eff_min <= dur <= eff_max for dur in durations):
        raise ValueError(
            f"plano fora dos limites remotos {eff_min}–{eff_max} ms: "
            f"{durations}")

    plan: list[tuple[int, int]] = []
    start = 0
    for dur in durations:
        plan.append((start, dur))
        start += dur
    return plan

def _slice_imu_csv(_services, imu_csv: str, start_ms: int, duration_ms: int) -> tuple[str, int]:
    """Recorta o imu.csv no intervalo do chunk e zera o relógio do pedaço."""
    start_ns = int(start_ms) * 1_000_000
    end_ns = start_ns + int(duration_ms) * 1_000_000
    lines = (imu_csv or "").splitlines()
    if not lines:
        return "", 0
    header = lines[0]
    out = [header]
    for line in lines[1:]:
        if not line.strip():
            continue
        first, _, rest = line.partition(",")
        try:
            t_ns = int(float(first))
        except ValueError:
            continue
        if t_ns < start_ns or t_ns >= end_ns:
            continue
        out.append(f"{t_ns - start_ns},{rest}" if rest else str(t_ns - start_ns))
    return "\n".join(out) + ("\n" if len(out) > 1 else ""), max(0, len(out) - 1)

def _chunk_video_signature(_services, src: Path, start_s: float, dur_s: float, *, preserve_pts: bool = False) -> str:
    stat = src.stat()
    value = (
        f"{'vfr1' if preserve_pts else 'v1'}|{src.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|"
        f"{float(start_s):.3f}|{float(dur_s):.3f}"
    )
    return _services.hashlib.sha256(value.encode("utf-8")).hexdigest()

def _chunk_video_path(_services, 
    src: Path,
    chunk_index: int,
    start_ms: int,
    duration_ms: int,
    *, preserve_pts: bool = False,
) -> Path:
    """Nome imutável: contas paralelas compartilham o mesmo corte pronto."""
    signature = _services._chunk_video_signature(
        src, start_ms / 1000.0, duration_ms / 1000.0, preserve_pts=preserve_pts)
    return src.with_name(
        f"{src.stem}_ch{int(chunk_index)}_{signature[:12]}{src.suffix}")

def _chunk_video_lock(_services, dest: Path) -> threading.Lock:
    key = _services.os.path.normcase(str(dest.resolve(strict=False)))
    with _services._CHUNK_VIDEO_LOCKS_GUARD:
        return _services._chunk_video_locks.setdefault(key, _services.threading.Lock())

def _cut_video_chunk(_services, src: Path, dest: Path, start_s: float, dur_s: float,
                     *, preserve_pts: bool = False) -> Path:
    """Corta uma vez e publica atomicamente para todas as contas do lote."""
    expected = _services._chunk_video_signature(src, start_s, dur_s, preserve_pts=preserve_pts)
    ready = dest.with_name(dest.name + ".chunk.ok")
    with _services._chunk_video_lock(dest):
        try:
            if (dest.is_file() and dest.stat().st_size > 0 and ready.is_file()
                    and ready.read_text(encoding="utf-8").strip() == expected):
                if preserve_pts:
                    _services._require_same_video_pts(src, dest, start_ns=round(start_s * 1e9),
                                            end_ns=round((start_s + dur_s) * 1e9))
                _services._record_generated_media(dest, root=dest.parent, role="delivery_chunk")
                return dest
        except OSError:
            pass
        ff = _services._ffmpeg_bin()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.stem + ".tmp.mp4")
        if tmp.exists():
            tmp.unlink()
        if preserve_pts:
            container = _services._measured_pts_container_args(tmp)
            filter_index = container.index("-vf") + 1
            container[filter_index] += (
                f",trim=start={start_s:.6f}:end={start_s + dur_s:.6f},setpts=PTS-STARTPTS")
            container[-1:-1] = _services._measured_packet_tail_args(
                src, start_ns=round(start_s * 1e9), end_ns=round((start_s + dur_s) * 1e9))
            with _services._cpu_slots(1):
                result = _services._ffmpeg_run([
                    *_services._ffmpeg_head(ff), "-i", str(src),
                    *_services._native_video_codec_args(nvenc=False), *container])
            if result.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise _services.UploadError("Falha no recorte Nymeria com PTS medidos.",
                                  transient=False, phase="prepare")
            try:
                _services._require_same_video_pts(src, tmp, start_ns=round(start_s * 1e9),
                                        end_ns=round((start_s + dur_s) * 1e9))
                tmp.replace(dest)
                ready.write_text(expected, encoding="utf-8")
                _services._record_generated_media(dest, root=dest.parent, role="delivery_chunk")
                return dest
            finally:
                tmp.unlink(missing_ok=True)
        cmd = [
            *_services._ffmpeg_head(ff),
            "-ss", f"{max(0.0, float(start_s)):.3f}",
            "-i", str(src),
            "-t", f"{max(0.1, float(dur_s)):.3f}",
            "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(tmp),
        ]
        res = _services._ffmpeg_run(cmd)
        if res.returncode != 0 or not _services.probe_video(tmp).get("duration_ms"):
            if tmp.exists():
                tmp.unlink()
            cmd = [
                *_services._ffmpeg_head(ff),
                "-ss", f"{max(0.0, float(start_s)):.3f}",
                "-i", str(src),
                "-t", f"{max(0.1, float(dur_s)):.3f}",
                *_services._native_video_codec_args(nvenc=_services._use_nvenc(ff)),
                *_services._native_container_args(tmp),
            ]
            res = _services._ffmpeg_run(cmd)
        if res.returncode != 0:
            if tmp.exists():
                tmp.unlink()
            raise _services.UploadError(
                f"corte de chunk falhou: {(res.stderr or '')[:400]}")
        tmp.replace(dest)
        ready.write_text(expected, encoding="utf-8")
        _services._record_generated_media(dest, root=dest.parent, role="delivery_chunk")
        return dest

def _build_sidecar(_services, item: dict[str, Any], session_id: str, log_id: str,
                   recorded_at: str, profile: DeviceProfile,
                   video_probe: dict[str, Any], imu_csv: str,
                   frames_csv: str, *, chunk_index: int = 0,
                   duration_ms: int | None = None) -> bytes:
    """Monta o sidecar .data.zip para uma identidade específica (por conta).

    Em modo device: device/platform/uptime/appVersion/calib/GOP vêm do celular
    físico conectado. Fora do modo device, o perfil da conta continua dono.
    """
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError

    duration_ms = int(duration_ms if duration_ms is not None else item["duration_ms"])
    wall_ms = (_services.device_profile.recorded_at_to_wall_ms(recorded_at)
               or int(_services.time.time() * 1000))
    try:
        if pb.device_mode_requires_physical():
            bound = pb.require_campaign_physical_binding(wall_ms=wall_ms)
            device_meta = bound["sidecar_device"]
            platform_meta = bound["sidecar_platform"]
            uptime_ns = bound["uptime_ns"]
            app_version = bound["app_version"]
            calib = bound["calib"]
            frames_gop = bound["frames_gop"]
        else:
            device_meta = profile.sidecar_device_meta()
            platform_meta = profile.sidecar_platform_meta()
            uptime_ns = profile.uptime_ns_at(wall_ms)
            app_version = None
            calib = profile.calib
            frames_gop = profile.frames_gop
    except PhysicalDeviceRequiredError as exc:
        raise _services.UploadError(
            str(exc), transient=False, phase="prepare") from exc
    return _services.build_sidecar_zip_custom(
        session_id=session_id, chunk_index=chunk_index, duration_ms=duration_ms,
        recorded_at=recorded_at, video_probe=video_probe, log_id=log_id,
        imu_csv=imu_csv, frames_csv=frames_csv,
        imu_sample_count=item.get("n_samples"),
        calib=calib,
        uptime_ns=uptime_ns,
        frames_gop=frames_gop,
        device_meta=device_meta,
        platform_meta=platform_meta,
        app_version=app_version,
        imu_diagnostics=(item.get("imu_diagnostics")
                         if isinstance(item.get("imu_diagnostics"), dict) else None),
    )

def _account_video_path(_services, base: Path, profile: DeviceProfile) -> Path:
    """`<stem>_acc<8 hex do device_id>.mp4` — um arquivo por aparelho, estável.

    Em modo device a chave é o SSAID do celular físico conectado (assinatura
    única do aparelho). Fora do modo device permanece o device_id do perfil.
    """
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError

    device_id = profile.device_id
    if pb.device_mode_requires_physical():
        try:
            device_id = pb.require_campaign_physical_binding()["device_id"]
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(
                str(exc), transient=False, phase="prepare") from exc
    tag = device_id.replace("-", "")[:8]
    return base.with_name(f"{base.stem}_acc{tag}.mp4")

def _lock_for_account_video(_services, path: Path) -> threading.Lock:
    key = str(path)
    with _services._ACCOUNT_VIDEO_LOCKS:
        lock = _services._account_video_locks.get(key)
        if lock is None:
            lock = _services.threading.Lock()
            _services._account_video_locks[key] = lock
        return lock

def _account_video_ok_path(_services, out: Path) -> Path:
    return out.with_name(out.name + ".ok")

def _account_video_tmp_path(_services, out: Path) -> Path:
    """Temporário atômico com extensão `.mp4`.

    `foo.mp4.tmp` faz o ffmpeg recusar o muxer (`Unable to choose an output
    format`). `foo.tmp.mp4` + `-f mp4` é o que o encode nativo do Ego4D já usa.
    """
    return out.with_name(f"{out.stem}.tmp.mp4")

def _valid_account_video(_services, out: Path, base: Path, *, preserve_pts: bool = False) -> bool:
    """Cache hit: arquivo completo, mais novo que o `_native.mp4`.

    O marcador `.ok` evita ffprobe em todo envio (abrir 500MB+ N vezes).
    Sem marcador (cache antigo), sonda uma vez e grava o `.ok`.
    """
    if not out.exists() or not base.exists():
        return False
    try:
        st = out.stat()
        if st.st_size <= 0 or st.st_mtime < base.stat().st_mtime:
            return False
        ok = _services._account_video_ok_path(out)
        if (not ok.exists() or ok.stat().st_mtime < st.st_mtime
                or ok.read_text(encoding="utf-8").strip()
                != _services._ACCOUNT_VIDEO_ENCODE_VERSION + ("-vfr1" if preserve_pts else "")):
            return False
        if preserve_pts:
            _services._require_same_video_pts(base, out)
        return bool(_services.probe_video(out).get("duration_ms"))
    except Exception:  # noqa: BLE001 — probe/stat falhou: trata como miss
        return False

def _per_account_video(_services, base: Path, profile: DeviceProfile, *, preserve_pts: bool = False) -> Path:
    """Vídeo POR APARELHO: re-encode com ABR/GOP do aparelho, cacheado no disco.

    Em modo device ABR/GOP e a chave de cache seguem o celular físico conectado.
    Fora do modo device, seguem o `device_id`/métricas do perfil da conta.

    Encode atômico (`*.tmp.mp4` → replace) para o upload não ler arquivo
    pela metade. Cache hit (mtime ≥ native) pula o ffmpeg: retry, reset do
    sent_registry e conta nova no mesmo clipe reaproveitam o trabalho.
    """
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError

    frames_gop = profile.frames_gop
    mb = profile.video_bitrate_mbps
    if pb.device_mode_requires_physical():
        try:
            bound = pb.require_campaign_physical_binding()
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(
                str(exc), transient=False, phase="prepare") from exc
        frames_gop = bound["frames_gop"]
        mb = bound["video_bitrate_mbps"]
    out = _services._account_video_path(base, profile)
    lock = _services._lock_for_account_video(out)
    with lock:
        if _services._valid_account_video(out, base, preserve_pts=preserve_pts):
            _services._record_generated_media(out, root=base.parent, role="account_video")
            return out
        with _services._cpu_slots(1):
            # Pode ter ficado pronto enquanto esta conta aguardava o lock/slot.
            if _services._valid_account_video(out, base, preserve_pts=preserve_pts):
                _services._record_generated_media(out, root=base.parent, role="account_video")
                return out
            ff = _services._ffmpeg_bin()
            br = f"{float(mb):.1f}M"
            tmp = _services._account_video_tmp_path(out)
            stale = out.with_name(out.name + ".tmp")  # legado .mp4.tmp
            for leftover in (tmp, stale):
                if leftover.exists():
                    leftover.unlink()
            input_args = [*_services._ffmpeg_head(ff), "-i", str(base)]
            common_args = (_services._measured_pts_container_args(tmp) if preserve_pts
                           else _services._native_container_args(tmp))
            if preserve_pts:
                common_args[-1:-1] = _services._measured_packet_tail_args(base)
            use_nvenc = _services._use_nvenc(ff)
            cmd = [
                *input_args,
                *_services._native_video_codec_args(
                    nvenc=use_nvenc, bitrate=br, gop=frames_gop),
                *common_args,
            ]
            try:
                res = _services._ffmpeg_run(cmd)
            except FileNotFoundError as exc:
                raise _services.UploadError(
                    "re-encode por conta precisa de ffmpeg") from exc
            if res.returncode != 0 and use_nvenc:
                if tmp.exists():
                    tmp.unlink()
                cmd = [
                    *input_args,
                    *_services._native_video_codec_args(
                        nvenc=False, bitrate=br, gop=frames_gop),
                    *common_args,
                ]
                res = _services._ffmpeg_run(cmd)
            probed = _services.probe_video(tmp) if res.returncode == 0 else {}
            if res.returncode != 0:
                if tmp.exists():
                    tmp.unlink()
                raise _services.UploadError(
                    f"re-encode por conta falhou: {res.stderr.strip()[:400]}")
            try:
                if not probed.get("duration_ms"):
                    size = tmp.stat().st_size if tmp.exists() else 0
                    raise _services.UploadError(
                        "re-encode por conta gerou vídeo sem duração "
                        f"(size={size})")
                if preserve_pts:
                    _services._require_same_video_pts(base, tmp)
                    if abs(int(probed["duration_ms"]) - int(_services.probe_video(base)["duration_ms"])) > 1:
                        raise _services.UploadError("Duração alterada durante encode Nymeria.",
                                          transient=False, phase="prepare")
                tmp.replace(out)
                _services._account_video_ok_path(out).write_text(
                    _services._ACCOUNT_VIDEO_ENCODE_VERSION + ("-vfr1" if preserve_pts else ""), encoding="utf-8")
                _services._record_generated_media(out, root=base.parent, role="account_video")
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
    return out

REQUIRES = ('ACCOUNT_ENCODE_WORKERS', 'MAX_DUR_MS', 'MIN_DUR_MS', 'NATIVE_BITRATE', 'Path', 'UploadError', '_ACCOUNT_ENCODE_RESERVATION_LOCK', '_ACCOUNT_ENCODE_SLOTS', '_ACCOUNT_VIDEO_ENCODE_VERSION', '_ACCOUNT_VIDEO_LOCKS', '_CHUNK_VIDEO_LOCKS_GUARD', '_NATIVE_CACHE_LOCKS', '_NATIVE_CACHE_LOCKS_GUARD', '_NATIVE_CACHE_VERSION', '_account_video_locks', '_account_video_ok_path', '_account_video_path', '_account_video_tmp_path', '_chunk_video_lock', '_chunk_video_locks', '_chunk_video_signature', '_cpu_slots', '_ego_clip_inputs', '_ego_prepare_plan', '_ffmpeg_bin', '_ffmpeg_encoder_available', '_ffmpeg_head', '_ffmpeg_run', '_frames_csv', '_heavy_encode_threads', '_lock_for_account_video', '_measured_packet_tail_args', '_measured_pts_container_args', '_measured_video_pts', '_native_cache_backup', '_native_cache_duration_ok', '_native_cache_fingerprint', '_native_cache_guard', '_native_cache_key', '_native_cache_marker', '_native_cache_marker_matches', '_native_cache_temp', '_native_container_args', '_native_video_codec_args', '_normalize_ego_clip_for_prepare', '_normalize_video', '_record_generated_media', '_require_same_video_pts', '_use_nvenc', '_valid_account_video', 'build_frames_csv_from_video', 'build_sidecar_zip_custom', 'config', 'device_profile', 'ego4d', 'ffmpeg_bin', 'hashlib', 'holoassist', 'json', 'math', 'nymeria', 'os', 'prepare_clip', 'probe_video', 'random', 'threading', 'time', 'uuid')
