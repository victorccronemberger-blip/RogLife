"""Admissão do leitor MP4 limitado para edit lists (elst).

Regressão da falha "Vídeo sem PTS medidos; preparo interrompido.": o native
Ego4D carrega um edit list trivial (1 entrada, media_time 0, rate 1.0) gravado
pelo ffmpeg e era rejeitado pelo leitor MP4, deixando os PTS dependentes de um
decode ffprobe inteiro (300 s sob carga).

Contrato: o leitor MP4 aceita trilhas sem offsets de composição (ctts) e sem
edit lists que desloquem o tempo. Um elst identidade não desloca nada; todo o
resto (shift, empty edit, rate != 1, múltiplas entradas, ctts) força ffprobe.
"""

from moneymin import sidecar


# — builders Byte-level (ISO-BMFF mínimo, só o que o leitor consome) —


def _u32(value: int) -> bytes:
    return int(value).to_bytes(4, "big")


def _box(btype: bytes, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + btype + payload


def _elst_payload(version: int, entries: list[tuple[int, int, int, int]]) -> bytes:
    # entry: (segment_duration, media_time, media_rate_int, media_rate_frac)
    body = bytes([version]) + b"\x00" * 3 + _u32(len(entries))
    for seg, media, rate_int, rate_frac in entries:
        if version == 1:
            body += int(seg).to_bytes(8, "big") + int(media).to_bytes(8, "big", signed=True)
        else:
            body += _u32(seg) + _u32(media & 0xFFFFFFFF)
        body += int(rate_int).to_bytes(2, "big") + int(rate_frac).to_bytes(2, "big")
    return body


def _stts_payload(runs: list[tuple[int, int]]) -> bytes:
    body = b"\x00" * 4 + _u32(len(runs))
    for count, delta in runs:
        body += _u32(count) + _u32(delta)
    return body


def _stss_payload(sync: list[int]) -> bytes:
    body = b"\x00" * 4 + _u32(len(sync))
    for sample in sync:
        body += _u32(sample)
    return body


def _ctts_payload(offsets: list[tuple[int, int]]) -> bytes:
    body = b"\x00" * 4 + _u32(len(offsets))
    for count, offset in offsets:
        body += _u32(count) + _u32(offset & 0xFFFFFFFF)
    return body


def _movie(*, edit_entries: list[tuple[int, int, int, int]] | None = None,
           edit_version: int = 0,
           composition_offsets: list[tuple[int, int]] | None = None,
           timescale: int = 90_000) -> bytes:
    kids = [_box(b"stts", _stts_payload([(2, 3000), (1, 4500)]))]
    if composition_offsets is not None:
        kids.append(_box(b"ctts", _ctts_payload(composition_offsets)))
    kids.append(_box(b"stss", _stss_payload([1, 3])))
    stbl = _box(b"stbl", b"".join(kids))
    minf = _box(b"minf", stbl)
    mdhd = _box(b"mdhd", b"\x00\x00\x00\x00" + _u32(0) + _u32(0)
                + _u32(timescale) + _u32(0) + b"\x00\x00\x00\x00")
    hdlr = _box(b"hdlr", b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00"
                + b"vide" + b"\x00" * 13)
    mdia = _box(b"mdia", mdhd + hdlr + minf)
    trak_kids: list[bytes] = []
    if edit_entries is not None:
        trak_kids.append(_box(b"edts",
                              _box(b"elst", _elst_payload(edit_version, edit_entries))))
    trak_kids.append(mdia)
    moov = _box(b"moov", _box(b"trak", b"".join(trak_kids)))
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2") + moov


# expected: 3 samples @90 kHz; stts 2×3000 + 1×4500; stss {1, 3}
REFERENCE = [(0, True), (33_333_333, False), (66_666_667, True)]


def test_elst_trivial_v0_is_identity():
    payload = _elst_payload(0, [(3000, 0, 1, 0)])
    assert sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_trivial_v1_is_identity():
    payload = _elst_payload(1, [(3000, 0, 1, 0)])
    assert sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_empty_edit_v0_is_not_identity():
    payload = _elst_payload(0, [(3000, 0xFFFFFFFF, 1, 0)])
    assert not sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_empty_edit_v1_is_not_identity():
    payload = _elst_payload(1, [(3000, -1, 1, 0)])
    assert not sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_shifted_media_time_is_not_identity():
    payload = _elst_payload(0, [(3000, 3000, 1, 0)])
    assert not sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_non_unit_rate_is_not_identity():
    payload = _elst_payload(0, [(3000, 0, 1, 32768)])
    assert not sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_multiple_entries_are_not_identity():
    payload = _elst_payload(0, [(1500, 0, 1, 0), (1500, 0, 1, 0)])
    assert not sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_unsupported_version_is_not_identity():
    payload = _elst_payload(2, [(3000, 0, 1, 0)])
    assert not sidecar._elst_is_trivial(payload, 0, len(payload))


def test_elst_truncated_is_not_identity():
    payload = _elst_payload(0, [(3000, 0, 1, 0)])
    assert not sidecar._elst_is_trivial(payload, 0, len(payload) - 3)


def test_reader_accepts_track_without_edit_list(tmp_path):
    video = tmp_path / "no_edit.mp4"
    video.write_bytes(_movie())
    assert sidecar._extract_frame_pts_mp4(video) == REFERENCE


def test_reader_accepts_trivial_edit_list_v0(tmp_path):
    video = tmp_path / "trivial_v0.mp4"
    video.write_bytes(_movie(edit_entries=[(3000, 0, 1, 0)], edit_version=0))
    assert sidecar._extract_frame_pts_mp4(video) == REFERENCE


def test_reader_accepts_trivial_edit_list_v1(tmp_path):
    video = tmp_path / "trivial_v1.mp4"
    video.write_bytes(_movie(edit_entries=[(3000, 0, 1, 0)], edit_version=1))
    assert sidecar._extract_frame_pts_mp4(video) == REFERENCE


def test_reader_rejects_shifted_edit_list(tmp_path):
    video = tmp_path / "shifted.mp4"
    video.write_bytes(_movie(edit_entries=[(3000, 3000, 1, 0)]))
    assert sidecar._extract_frame_pts_mp4(video) == []


def test_reader_rejects_empty_edit_edit_list(tmp_path):
    video = tmp_path / "empty.mp4"
    video.write_bytes(_movie(edit_entries=[(3000, 0xFFFFFFFF, 1, 0)]))
    assert sidecar._extract_frame_pts_mp4(video) == []


def test_reader_rejects_non_unit_rate_edit_list(tmp_path):
    video = tmp_path / "rate.mp4"
    video.write_bytes(_movie(edit_entries=[(3000, 0, 1, 32768)]))
    assert sidecar._extract_frame_pts_mp4(video) == []


def test_reader_rejects_multi_entry_edit_list(tmp_path):
    video = tmp_path / "multi.mp4"
    video.write_bytes(_movie(edit_entries=[(1500, 0, 1, 0), (1500, 0, 1, 0)]))
    assert sidecar._extract_frame_pts_mp4(video) == []


def test_reader_rejects_composition_offsets_with_trivial_edit_list(tmp_path):
    video = tmp_path / "ctts.mp4"
    video.write_bytes(_movie(edit_entries=[(3000, 0, 1, 0)],
                             composition_offsets=[(3, 0)]))
    assert sidecar._extract_frame_pts_mp4(video) == []