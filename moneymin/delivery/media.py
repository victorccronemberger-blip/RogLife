"""Media upload protocol core with declared collaborators."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class MediaServices:
    BACKLOG_CAP_MS: Any
    Path: Any
    UploadError: Any
    _http_upload_error: Any
    _normalize_recorded_at_sequence: Any
    _probe_duration_ms: Any
    _run_ffmpeg: Any
    _status_from_exception: Any
    _video_dims: Any
    _video_handler: Any
    _video_pix_fmt: Any
    config: Any
    ffmpeg_bin: Any
    format_recorded_at: Any
    probe_video: Any
    recorded_at_to_wall_ms: Any
    time: Any
    transport: Any
    urllib: Any

REQUIRES = ('BACKLOG_CAP_MS', 'Path', 'UploadError', '_http_upload_error', '_normalize_recorded_at_sequence', '_probe_duration_ms', '_run_ffmpeg', '_status_from_exception', '_video_dims', '_video_handler', '_video_pix_fmt', 'config', 'ffmpeg_bin', 'format_recorded_at', 'probe_video', 'recorded_at_to_wall_ms', 'time', 'transport', 'urllib')

def default_device_meta(_services, profile: DeviceProfile | None = None) -> dict[str, Any]:
    """Metadados de dispositivo que o app nativo envia em meta.device.

    Formato curto Android (getDeviceUploadMeta): APENAS {"model": Build.MODEL}.
    NÃO envia systemName nem systemVersion no POST /uploads.
    Em modo device: Build.MODEL do celular físico conectado (dinâmico).
    Fora do modo device: perfil da conta ou default global.
    """
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError

    if pb.device_mode_requires_physical():
        try:
            return pb.require_campaign_physical_binding()["upload_device"]
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(str(exc), transient=False, phase="create") from exc
    if profile is not None:
        return profile.upload_device_meta()
    return {
        "model": _services.config.NATIVE_DEVICE_MODEL,
    }
def default_platform_meta(_services, profile: DeviceProfile | None = None) -> dict[str, Any]:
    """Metadados de plataforma que o app nativo envia em meta.platform.

    Formato curto Android (getDeviceUploadMeta): APENAS {"os": "android"}.
    NÃO envia version no POST /uploads.
    Em modo device exige identidade física utilizável (mesmo contrato do device).
    """
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError

    if pb.device_mode_requires_physical():
        try:
            return pb.require_campaign_physical_binding()["upload_platform"]
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(str(exc), transient=False, phase="create") from exc
    if profile is not None:
        return profile.upload_platform_meta()
    return {
        "os": _services.config.NATIVE_PLATFORM_OS,
    }


def default_app_version(_services, profile: DeviceProfile | None = None) -> str:
    """appVersion curto do POST /uploads: Minute do celular em modo device."""
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError

    if pb.device_mode_requires_physical():
        try:
            return pb.require_campaign_physical_binding()["app_version"]
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(str(exc), transient=False, phase="create") from exc
    return str(_services.config.APP_VERSION)
def _iso_now(_services, ) -> str:
    return _services.format_recorded_at(_services.time.time())
def _normalize_recorded_at_sequence(_services, 
    values: list[str],
    durations_ms: list[int],
    *,
    now: float | None = None,
) -> list[str]:
    """Canoniza horários explícitos sem destruir a distância entre chunks.

    O chamador já usou esses mesmos horários para montar os sidecars. Portanto
    não podemos prender cada chunk à duração total da sessão: isso colapsa os
    últimos horários e faz o POST divergir de ``metadata.createdAt``. Valores
    explícitos são validados como uma sequência e apenas reformatados.
    """
    if len(values) != len(durations_ms):
        raise _services.UploadError(
            "recorded_at precisa ter exatamente um horário por chunk",
            transient=False,
        )
    if not values:
        return []
    wall_ms: list[int] = []
    for value in values:
        parsed = _services.recorded_at_to_wall_ms(str(value))
        if parsed is None:
            raise _services.UploadError(
                f"recorded_at inválido: {value!r}", transient=False)
        wall_ms.append(parsed)
    if any(current <= previous
           for previous, current in zip(wall_ms, wall_ms[1:], strict=False)):
        raise _services.UploadError(
            "recorded_at dos chunks precisa ser estritamente crescente",
            transient=False,
        )
    now_ms = int((_services.time.time() if now is None else float(now)) * 1000)
    backlog_cap_ms = int(
        _services.config.recording_limits().get("backlog_cap_ms") or _services.BACKLOG_CAP_MS)
    if wall_ms[0] < now_ms - backlog_cap_ms:
        raise _services.UploadError(
            "recorded_at está fora do backlog de gravação do Minute",
            transient=False,
        )
    last_end_ms = max(
        started + max(0, int(duration))
        for started, duration in zip(wall_ms, durations_ms, strict=True)
    )
    # Pequena tolerância só para diferença de relógio entre captura e chamada.
    if last_end_ms > now_ms + 5_000:
        raise _services.UploadError(
            "recorded_at indica chunk que ainda não terminou",
            transient=False,
        )
    return [_services.format_recorded_at(value / 1000.0) for value in wall_ms]
def _recorded_at_sequence_from_base(_services, 
    base: str,
    durations_ms: list[int],
    *,
    now: float | None = None,
) -> list[str]:
    """Expande o início da sessão em inícios contínuos de cada chunk."""
    base_ms = _services.recorded_at_to_wall_ms(base)
    if base_ms is None:
        raise _services.UploadError(f"recorded_at inválido: {base!r}", transient=False)
    cursor = base_ms
    values: list[str] = []
    for duration in durations_ms:
        values.append(_services.format_recorded_at(cursor / 1000.0))
        cursor += max(0, int(duration))
    return _services._normalize_recorded_at_sequence(values, durations_ms, now=now)
def default_video_meta(_services, ) -> dict[str, Any]:
    """Metadados de vídeo que o app nativo envia em meta.video.

    Shape Android: {height, path, rotationDeg, width} (path = {logId}.mp4).
    """
    return {
        "height": 1080,
        "path": "",
        "rotationDeg": 0,
        "width": 1440,
    }
def default_network_meta(_services, ) -> dict[str, Any]:
    """Metadados de rede (wifi|cellular) — no modo device, amostra do celular."""
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError

    if pb.device_mode_requires_physical():
        try:
            bound = pb.require_campaign_physical_binding()
            _network_type, network_meta = pb.require_physical_network(bound)
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(str(exc), transient=False, phase="create") from exc
        return dict(network_meta)
    return {
        "type": "wifi",
        "carrier": None,
    }
def _put_blob(_services, blob_url: str, file_bytes: bytes, content_type: str = "video/mp4",
              timeout: int = 300, resumable: bool | None = None,
              on_progress: Callable[[int, int, float], None] | None = None) -> int:
    """Faz PUT dos bytes no Azure Blob Storage. Devolve o status HTTP.

    O transporte (e o fingerprint de rede) é do `transport.py`: com curl_cffi
    o MP4 e o sidecar sobem em blocos de 4MB + Put Block List, como o
    AzureBlockUploader/OkHttp do app Android. Sem curl_cffi, PUT BlockBlob
    único. Sucesso é 201 Created (o OkHttp do app aceita qualquer 2xx).

    Levanta UploadError se o Azure recusar (status != 201) ou em erro de rede.
    """
    try:
        if on_progress is not None:
            return _services.transport.put_blob(blob_url, file_bytes,
                                      content_type=content_type, timeout=timeout,
                                      resumable=resumable, on_progress=on_progress)
        return _services.transport.put_blob(blob_url, file_bytes,
                                  content_type=content_type, timeout=timeout,
                                  resumable=resumable)
    except _services.urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise _services._http_upload_error("PUT Blob", exc.code, body) from exc
    except _services.UploadError:
        raise
    except Exception as exc:
        status = _services._status_from_exception(exc)
        if status is not None:
            raise _services._http_upload_error("PUT Blob", status, str(exc)) from exc
        raise _services.UploadError(
            f"PUT Blob falhou (erro de rede): {exc}",
            transient=True, phase="transport",
        ) from exc
def _put_blob_file(_services, blob_url: str, file_path: str | Path,
                   content_type: str = "video/mp4",
                   timeout: int = 300,
                   on_progress: Callable[[int, int, float], None] | None = None,
                   *, expected_sha256: str | None = None) -> int:
    """PUT do vídeo a partir do disco, sem uma cópia integral na RAM."""
    try:
        if expected_sha256 is not None:
            return _services.transport.put_blob_file(blob_url, file_path,
                                           content_type=content_type, timeout=timeout,
                                           on_progress=on_progress, expected_sha256=expected_sha256)
        return _services.transport.put_blob_file(blob_url, file_path,
                                       content_type=content_type,
                                       timeout=timeout,
                                       on_progress=on_progress)
    except _services.transport.TransportIntegrityError:
        raise _services.UploadError("O conteúdo local mudou; o recibo e os arquivos foram preservados para revisão.",
                          transient=False, phase="transport", review_required=True) from None
    except _services.urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise _services._http_upload_error("PUT Blob", exc.code, body) from exc
    except _services.UploadError:
        raise
    except Exception as exc:
        status = _services._status_from_exception(exc)
        if status is not None:
            raise _services._http_upload_error("PUT Blob", status, str(exc)) from exc
        raise _services.UploadError(
            f"PUT Blob falhou (erro de rede): {exc}",
            transient=True, phase="transport",
        ) from exc
def _probe_duration_ms(_services, video_path: str | Path) -> int:
    """Duração em ms: ffprobe, senão `ffmpeg -i` (imageio), senão PyAV."""
    return int(_services.probe_video(video_path).get("duration_ms") or 0)
def _video_pix_fmt(_services, video_path: str | Path) -> tuple[str | None, str | None]:
    """pix_fmt / color_range via probe unificado (ffprobe ou ffmpeg -i)."""
    info = _services.probe_video(video_path)
    return info.get("pix_fmt"), info.get("color_range")
def _video_handler(_services, video_path: str | Path) -> str | None:
    """handler_name do stream de vídeo (ex.: 'VideoHandler')."""
    return _services.probe_video(video_path).get("handler_name")
def _run_ffmpeg(_services, cmd: list[str], timeout: int = 3600):
    """Roda ffmpeg sem stdin (evita pausa) e sem console no Windows."""
    import os
    import subprocess
    kwargs: dict[str, Any] = {
        "capture_output": True, "text": True, "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, **kwargs)
def normalize_video(_services, video_path: str | Path, out_dir: str | Path | None = None,
                    preset: str = "veryfast") -> Path:
    """Reencoda o vídeo para o formato EGO nativo do app Android (1440x1080 4:3).

    O app grava a ultra-wide em 1440x1080 (4:3); datasets/YouTube vêm em
    16:9 (1920x1080). O Catbear pontua melhor (clarity=great) o que replica a
    gravação Android: proporção 4:3, yuv420p limited-range, H.264 High@4.2
    (MediaRecorder setVideoEncodingProfileLevel(8, 8192)), handler
    VideoHandler/SoundHandler e áudio AAC 48 kHz estéreo 256 kbps.

    Conversão: escala para cobrir 1440x1080 (force_original_aspect_ratio=increase)
    e corta o excesso (crop) para 4:3 — conteúdo central preservado.

    Devolve o caminho pronto para upload (o próprio arquivo se já for
    compatível, ou um arquivo `_yuv420p.mp4` ao lado).
    Levanta UploadError se precisar reencodar e ffmpeg faltar/falhar.
    """
    video_path = _services.Path(video_path)
    pix_fmt, _color_range = _services._video_pix_fmt(video_path)
    handler = _services._video_handler(video_path) or ""
    w, h = _services._video_dims(video_path)
    native_43 = abs((w / h) - (4.0 / 3.0)) < 0.02
    needs = (pix_fmt == "yuvj420p" or "videohandler" not in handler.lower()
             or not native_43 or w != 1440)
    if not needs:
        return video_path

    out_dir = _services.Path(out_dir) if out_dir else video_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{video_path.stem}_yuv420p.mp4"
    if out.exists():
        out.unlink()
    cmd = [
        _services.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-v", "error",
        "-i", str(video_path),
        "-c:v", "libx264", "-preset", preset,
        "-b:v", "8000k", "-maxrate", "8000k", "-bufsize", "16000k",
        "-profile:v", "high", "-level:v", "4.2",
        "-g", "30", "-keyint_min", "30",
        "-vf", ("scale=1440:1080:force_original_aspect_ratio=increase,"
                "crop=1440:1080,"
                "scale=in_range=full:out_range=tv,format=yuv420p"),
        "-r", "30",
        "-color_range", "tv", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-metadata:s:v:0", "handler_name=VideoHandler",
        "-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "256k",
        "-metadata:s:a:0", "handler_name=SoundHandler",
        "-movflags", "+faststart", "-f", "mp4", str(out),
    ]
    try:
        res = _services._run_ffmpeg(cmd)
    except FileNotFoundError as exc:
        raise _services.UploadError(
            "vídeo precisa de reencode para formato Android (1440x1080 yuv420p), "
            "mas o FFmpeg do QMoney não está disponível; use Reparar instalação "
            "na aba Integrações") from exc
    if res.returncode != 0:
        raise _services.UploadError(
            f"falha ao normalizar vídeo para formato Android: {res.stderr.strip()[:300]}")
    return out
def _video_dims(_services, video_path: str | Path) -> tuple[int, int]:
    """Resolução (width, height) do stream de vídeo."""
    info = _services.probe_video(video_path)
    return int(info.get("width") or 0), int(info.get("height") or 0)
def trim_video(_services, 
    video_path: str | Path,
    cut_first: float = 30.0,
    cut_last: float = 30.0,
    auto: bool = True,
    out_dir: str | Path | None = None,
    margin: float = 1.0,
) -> str:
    """Corta intro/outro de vídeos (30s início + 30s fim por padrão).

    Padrão: remove 30 segundos do início e 30 segundos do fim — prático,
    rápido e seguro para vídeos de YouTube que sempre têm intro/outro.

    Devolve o caminho do vídeo cortado (o próprio arquivo se não precisou corte,
    ou um arquivo _trimmed.mp4 ao lado).
    """
    video_path = _services.Path(video_path)
    if not video_path.exists():
        raise _services.UploadError(f"vídeo não encontrado: {video_path}")

    duration_ms = _services._probe_duration_ms(video_path)
    if not duration_ms:
        raise _services.UploadError(f"não foi possível ler a duração de {video_path}")
    duration = duration_ms / 1000.0

    start = cut_first
    end = duration - cut_last

    # Se o vídeo for muito curto, não cortar
    if end <= start:
        return str(video_path)

    # Cortar
    out_dir = _services.Path(out_dir) if out_dir else video_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{video_path.stem}_trimmed.mp4"
    if out.exists():
        out.unlink()

    cmd = [
        _services.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-v", "error",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(end - start),
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", "8000k", "-maxrate", "8000k", "-bufsize", "16000k",
        "-profile:v", "high", "-level:v", "4.2",
        "-g", "30", "-keyint_min", "30",
        "-vf", ("scale=1440:1080:force_original_aspect_ratio=increase,"
                "crop=1440:1080,"
                "scale=in_range=full:out_range=tv,format=yuv420p"),
        "-r", "30",
        "-color_range", "tv", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-metadata:s:v:0", "handler_name=VideoHandler",
        "-c:a", "aac", "-ac", "2", "-ar", "48000", "-b:a", "256k",
        "-metadata:s:a:0", "handler_name=SoundHandler",
        "-movflags", "+faststart", "-f", "mp4",
        str(out),
    ]
    try:
        res = _services._run_ffmpeg(cmd)
    except FileNotFoundError as exc:
        raise _services.UploadError("ffmpeg não está instalado") from exc
    if res.returncode != 0:
        raise _services.UploadError(f"falha ao cortar vídeo: {res.stderr.strip()[:300]}")

    return str(out)
