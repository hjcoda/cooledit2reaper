"""Convert legacy Cool Edit / Audition ``.ses`` sessions to REAPER ``.rpp``.

Standard library only.  The input container is undocumented; the layouts used
here were reverse-engineered from real Cool Edit Pro 2.00 sessions and are
documented in ``docs/superpowers/specs/2026-11-24-ses-to-rpp-design.md``.
"""

from dataclasses import dataclass
import argparse
import base64
import math
import os
import struct
import sys
import uuid

MAGIC = b"COOLNESS"
LISTFILE_TAG = b"LISTFILE"
AUDIO_EXTENSIONS = (
    ".wav", ".wave", ".mp3", ".wma", ".aif", ".aiff", ".flac", ".ogg",
    ".caf", ".au", ".sd2", ".ape", ".mp2", ".m4a",
)


@dataclass
class Track:
    index: int
    name: str
    left: float
    right: float
    mute: bool
    solo: bool


@dataclass
class Clip:
    index: int
    track: int
    start: int
    length: int
    handle: int
    source_in: int
    path: str | None
    name: str
    gain: float


@dataclass
class EqBands:
    low_freq: float
    low_gain: float
    mid_freq: float
    mid_gain: float
    q: float
    high_freq: float
    high_gain: float


@dataclass
class Session:
    ses_path: str
    sample_rate: int
    tempo: tuple[float, int, int]
    tracks: list[Track]
    clips: list[Clip]
    cues: list[tuple[int, int, str]]
    loops: dict[int, dict]
    clip_gain: dict[int, dict]
    eq: dict[int, EqBands]
    master_left: float
    master_right: float


REAEQ_SIGNATURE = (
    b"qeer\xee^\xed\xfe\x02\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00"
    b"\x02\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00"
)
REAEQ_SUBHEADER = b"\x01\x00\x00\x00\x00\x00\x10\x00"


# ---------------------------------------------------------------------------
# Bounded little-endian readers
# ---------------------------------------------------------------------------

def _require(buf, off, size):
    if not isinstance(off, int) or not isinstance(size, int):
        raise ValueError("offset and size must be integers")
    if off < 0 or size < 0 or off + size > len(buf):
        raise ValueError(
            "read of %d bytes at offset %d exceeds %d-byte buffer"
            % (size, off, len(buf))
        )


def u32(buf, off):
    _require(buf, off, 4)
    return struct.unpack_from("<I", buf, off)[0]


def i32(buf, off):
    _require(buf, off, 4)
    return struct.unpack_from("<i", buf, off)[0]


def f64(buf, off):
    _require(buf, off, 8)
    return struct.unpack_from("<d", buf, off)[0]


def f32(buf, off):
    _require(buf, off, 4)
    return struct.unpack_from("<f", buf, off)[0]


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

def _parse_chunk_header(data, pos):
    """Return ``(id, body_offset, body_length)`` or ``None`` if unusable."""
    end = len(data)
    if pos + 8 > end:
        return None
    if data[pos:pos + 8] == LISTFILE_TAG:
        if pos + 12 > end:
            return None
        body_len = u32(data, pos + 8)
        body_off = pos + 12
        chunk_id = LISTFILE_TAG.decode("ascii")
    else:
        raw_id = data[pos:pos + 4]
        if len(raw_id) != 4 or not all(0x20 <= b < 0x7F for b in raw_id):
            return None
        chunk_id = raw_id.decode("ascii")
        body_len = u32(data, pos + 4)
        body_off = pos + 8
    if body_off + body_len > end:
        return None
    return chunk_id, body_off, body_len


def walk_chunks(data):
    """Return ``{chunk_id: (body_bytes, body_offset)}`` for a session.

    First occurrence of each id wins.  A single pad byte between chunks is
    tolerated.  Reading stops cleanly at truncation or trailing garbage.
    """
    if not data.startswith(MAGIC):
        raise ValueError("not a Cool Edit session (missing COOLNESS magic)")
    if len(data) < 12:
        raise ValueError("COOLNESS header is truncated")

    chunks = {}
    pos = 12
    while pos < len(data):
        header = _parse_chunk_header(data, pos)
        if header is None:
            # Tolerate exactly one pad byte before the next valid chunk.
            header = _parse_chunk_header(data, pos + 1)
            if header is None:
                break
        chunk_id, body_off, body_len = header
        if chunk_id not in chunks:
            chunks[chunk_id] = (data[body_off:body_off + body_len], body_off)
        pos = body_off + body_len
    return chunks


# ---------------------------------------------------------------------------
# Media map
# ---------------------------------------------------------------------------

def _extract_path(raw):
    """Return a usable Windows path from a ``wav `` record's raw text."""
    text = raw.split(b"\x00", 1)[0].decode("latin1", "replace")
    text = "".join(ch for ch in text if ch >= " " or ch == "\t")
    if not text:
        return None
    anchored = (
        (len(text) >= 3 and text[1] == ":" and text[0].isalpha())
        or text.startswith("\\\\")
        or text.startswith("//")
    )
    if anchored:
        return text
    if text.lower().endswith(AUDIO_EXTENSIONS):
        return text
    return None


def decode_media(data, chunks):
    """Return ``{file_pointer: windows_path}`` from the first LISTFILE body."""
    entry = chunks.get(LISTFILE_TAG.decode("ascii"))
    if entry is None:
        return {}
    body = entry[0]
    media = {}
    pos = 0
    while True:
        start = body.find(b"wav ", pos)
        if start < 0 or start + 16 > len(body):
            break
        record_len = u32(body, start + 4)
        pointer = u32(body, start + 8)
        # The path starts 8 bytes into the record body (after pointer and a
        # constant field) and is NUL-terminated inside the record's bounds.
        record_end = min(start + 8 + record_len, len(body))
        raw = body[start + 16:record_end]
        path = _extract_path(raw)
        if path is not None and pointer not in media:
            media[pointer] = path
        # Advance past this record when its length is trustworthy.
        if record_len and start + 8 + record_len <= len(body):
            pos = start + 8 + record_len
        else:
            pos = start + 4
    return media


# ---------------------------------------------------------------------------
# Session decoders
# ---------------------------------------------------------------------------

def _warn(message):
    print(f"warning: {message}", file=sys.stderr)


def _decode_name(raw):
    return raw.split(b"\x00", 1)[0].decode("latin1", "replace")


def _basename(path):
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def decode_tracks(chunks):
    entry = chunks.get("trks")
    if entry is None:
        return []
    body = entry[0]

    # Cool Edit Pro 2.x layout: count + N * 96-byte records.
    if len(body) >= 4 and (len(body) - 4) % 96 == 0:
        count = u32(body, 0)
        expected = (len(body) - 4) // 96
        if count == expected:
            tracks = []
            for idx in range(count):
                off = 4 + idx * 96
                try:
                    left = f64(body, off + 0)
                    right = f64(body, off + 8)
                    name = _decode_name(body[off + 20:off + 60]) or f"Track {idx + 1}"
                except ValueError:
                    _warn(f"skipping malformed CEP2 track record at index {idx}")
                    continue
                tracks.append(
                    Track(
                        index=idx + 1,
                        name=name,
                        left=left,
                        right=right,
                        mute=False,
                        solo=False,
                    )
                )
            return tracks

    # Audition 1.x layout: N * 152-byte records, no leading count.
    if len(body) > 0 and len(body) % 152 == 0:
        count = len(body) // 152
        tracks = []
        for idx in range(count):
            off = idx * 152
            try:
                volume = f64(body, off + 4)
                mute = i32(body, off + 20) == 1
                name = _decode_name(body[off + 24:off + 64]) or f"Track {idx + 1}"
            except ValueError:
                _warn(f"skipping malformed Audition track record at index {idx}")
                continue
            tracks.append(
                Track(
                    index=idx + 1,
                    name=name,
                    left=volume,
                    right=volume,
                    mute=mute,
                    solo=False,
                )
            )
        return tracks

    _warn("unsupported track table layout in trks chunk")
    return []


def gains_to_volpan(left, right):
    left = max(float(left), 0.0)
    right = max(float(right), 0.0)
    volume = max(left, right)
    if volume <= 0.0:
        return 0.0, 0.0

    eps = max(1e-12, volume * 1e-9)
    if abs(left - right) <= eps:
        pan = 0.0
    elif right <= eps:
        pan = -1.0
    elif left <= eps:
        pan = 1.0
    elif right > left:
        pan = 1.0 - (left / right)
    else:
        pan = (right / left) - 1.0

    pan = max(-1.0, min(1.0, pan))
    return volume, pan


def decode_clips(chunks, media):
    entry = chunks.get("blk ")
    if entry is None:
        return []
    body = entry[0]
    if len(body) < 8:
        return []

    count = u32(body, 0)

    # Two observed variants:
    # - plan/synthetic: records start at body+8, fields at +28/+32/... 
    # - real CEP2 files: records start at body+4, fields shifted by +4.
    if len(body) >= 8 + count * 88:
        rec_base = 8
        off_start = 28
        off_length = 32
        off_handle = 36
        off_pointer = 44
        off_track = 48
        off_source = 60
    elif len(body) >= 4 + count * 88:
        rec_base = 4
        off_start = 32
        off_length = 36
        off_handle = 40
        off_pointer = 48
        off_track = 52
        off_source = 64
    else:
        # Truncated input: choose the best effort start and clip count.
        rec_base = 8 if len(body) >= 8 else 4
        available = max((len(body) - rec_base) // 88, 0)
        if count > available:
            _warn(f"blk count {count} exceeds available records {available}; truncating")
            count = available
        off_start = 28 if rec_base == 8 else 32
        off_length = 32 if rec_base == 8 else 36
        off_handle = 36 if rec_base == 8 else 40
        off_pointer = 44 if rec_base == 8 else 48
        off_track = 48 if rec_base == 8 else 52
        off_source = 60 if rec_base == 8 else 64

    clips = []
    for idx in range(count):
        off = rec_base + idx * 88
        try:
            gain = f64(body, off + 0)
            start = i32(body, off + off_start)
            length = i32(body, off + off_length)
            handle = u32(body, off + off_handle)
            pointer = u32(body, off + off_pointer)
            track = i32(body, off + off_track)
            source_in = i32(body, off + off_source)
        except ValueError:
            _warn(f"skipping malformed clip record at index {idx}")
            continue

        path = media.get(pointer)
        name = _basename(path) if path else f"Clip {idx + 1}"
        clips.append(
            Clip(
                index=idx + 1,
                track=track,
                start=start,
                length=length,
                handle=handle,
                source_in=source_in,
                path=path,
                name=name,
                gain=gain,
            )
        )
    return clips


def decode_tempo(chunks):
    default = (120.0, 4, 4)
    entry = chunks.get("tmpo")
    if entry is None:
        return default
    body = entry[0]
    try:
        bpm = float(f64(body, 0))
        beats_per_bar = int(u32(body, 8))
        subdivisions = int(u32(body, 12))
    except ValueError:
        return default

    if beats_per_bar <= 0:
        return bpm, 4, 4
    denominator = 4
    if subdivisions > 0 and subdivisions % beats_per_bar == 0:
        candidate = subdivisions // beats_per_bar
        if candidate in {1, 2, 4, 8, 16, 32, 64}:
            denominator = candidate
    return bpm, beats_per_bar, denominator


def decode_cues(chunks):
    entry = chunks.get("cues")
    if entry is None:
        return []
    body = entry[0]
    if len(body) < 4:
        return []

    count = u32(body, 0)
    available = max((len(body) - 4) // 24, 0)
    count = min(count, available)
    out = []
    for idx in range(count):
        off = 4 + idx * 24
        try:
            position = i32(body, off + 0)
            duration = i32(body, off + 4)
            name = _decode_name(body[off + 12:off + 24])
        except ValueError:
            _warn(f"skipping malformed cue record at index {idx}")
            continue
        out.append((position, duration, name))
    return out


def decode_loops(chunks, clips):
    loop_entry = chunks.get("loop")
    bk20_entry = chunks.get("bk20")
    if loop_entry is None or bk20_entry is None:
        return {}

    loop_body = loop_entry[0]
    if len(loop_body) < 4:
        return {}
    count = u32(loop_body, 0)
    available = max((len(loop_body) - 4) // 4, 0)
    count = min(count, available)
    loop_handles = {u32(loop_body, 4 + i * 4) for i in range(count)}
    if not loop_handles:
        return {}

    bk20 = bk20_entry[0]
    if len(bk20) < 8:
        return {}
    rec_count = u32(bk20, 0)
    rec_size = u32(bk20, 4)
    if rec_size <= 0:
        return {}
    available = max((len(bk20) - 8) // rec_size, 0)
    rec_count = min(rec_count, available)

    extent_by_handle = {}
    for idx in range(rec_count):
        off = 8 + idx * rec_size
        if off + 32 > len(bk20):
            break
        handle = u32(bk20, off + 0)
        extent = i32(bk20, off + 28)
        extent_by_handle[handle] = extent

    clip_index_by_handle = {clip.handle: clip.index for clip in clips}
    out = {}
    for handle in loop_handles:
        clip_index = clip_index_by_handle.get(handle)
        extent = extent_by_handle.get(handle)
        if clip_index is None or extent is None:
            continue
        if extent > 0:
            out[clip_index] = {"extent": extent}
    return out


def _all_point_values(points, target):
    if not points:
        return True
    return all(abs(value - target) <= 1e-9 for _, value in points)


def decode_clip_gain(chunks, clips):
    out = {}

    # Static clip gain from blk: carry only non-identity values.
    for clip in clips:
        if abs(clip.gain - 1.0) > 1e-9:
            out.setdefault(clip.index, {})["volume"] = clip.gain

    # Map envelope record handle to clip index, preferring blk2 order.
    handle_to_clip = {}
    blk2_entry = chunks.get("blk2")
    if blk2_entry is not None and len(clips) > 0:
        body = blk2_entry[0]
        if len(body) >= 4:
            count = u32(body, 0)
            payload = len(body) - 4
            if count > 0 and payload >= count * 4 and payload % count == 0:
                stride = payload // count
                linked = min(count, len(clips))
                for idx in range(linked):
                    off = 4 + idx * stride
                    if off + 4 <= len(body):
                        handle_to_clip[u32(body, off)] = clips[idx].index

    for clip in clips:
        handle_to_clip.setdefault(clip.handle, clip.index)

    ep20_entry = chunks.get("ep20")
    if ep20_entry is None:
        return {k: v for k, v in out.items() if v}

    body = ep20_entry[0]
    if len(body) < 4:
        return {k: v for k, v in out.items() if v}
    record_count = u32(body, 0)
    off = 4

    for _ in range(record_count):
        if off + 16 > len(body):
            break
        _marker = u32(body, off + 0)
        _version = u32(body, off + 4)
        handle = u32(body, off + 8)
        group_count = u32(body, off + 12)
        off += 16

        volume_points = None
        pan_points = None
        for _ in range(group_count):
            if off + 12 > len(body):
                off = len(body)
                break
            point_count = u32(body, off + 0)
            parameter_id = u32(body, off + 4)
            _reserved = u32(body, off + 8)
            off += 12

            points = []
            max_points = max((len(body) - off) // 12, 0)
            take = min(point_count, max_points)
            for _ in range(take):
                sample_offset = u32(body, off + 0)
                value = f64(body, off + 4)
                off += 12
                points.append((sample_offset, value))
            if take < point_count:
                off = len(body)

            if parameter_id == 1:
                volume_points = points
            elif parameter_id == 2:
                pan_points = points

        clip_index = handle_to_clip.get(handle)
        if clip_index is None:
            continue

        item = out.setdefault(clip_index, {})
        if volume_points is not None and not _all_point_values(volume_points, 1.0):
            item["volume_points"] = volume_points
        if pan_points is not None and not _all_point_values(pan_points, 0.5):
            item["pan_points"] = pan_points

    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# Track EQ / ReaEQ
# ---------------------------------------------------------------------------

def decode_eq(chunks):
    entry = chunks.get("tkeq")
    if entry is None:
        return {}
    body = entry[0]
    if len(body) < 4:
        return {}

    count = u32(body, 0)
    available = max((len(body) - 4) // 88, 0)
    count = min(count, available)
    out = {}
    for idx in range(count):
        off = 4 + idx * 88
        try:
            track = u32(body, off + 0)
            low_freq = f64(body, off + 8)
            low_gain = f64(body, off + 16)
            high_freq = f64(body, off + 24)
            mid_gain = f64(body, off + 32)
            mid_freq = f64(body, off + 40)
            q = f64(body, off + 48)
            high_gain = f64(body, off + 56)
        except ValueError:
            _warn(f"skipping malformed tkeq record at index {idx}")
            continue

        if abs(low_gain) <= 1e-9 and abs(mid_gain) <= 1e-9 and abs(high_gain) <= 1e-9:
            continue
        out[track] = EqBands(
            low_freq=low_freq,
            low_gain=low_gain,
            mid_freq=mid_freq,
            mid_gain=mid_gain,
            q=q,
            high_freq=high_freq,
            high_gain=high_gain,
        )
    return out


def q_to_bandwidth(q):
    if q <= 0:
        return 2.0
    return math.asinh(1.0 / (2.0 * q)) * 2.0 / math.log(2.0)


def _db_to_linear(db):
    return 10.0 ** (db / 20.0)


def build_reaeq_state(bands):
    filters = [
        # low shelf
        (0, 1, float(bands.low_freq), _db_to_linear(float(bands.low_gain)), 2.0, 0),
        # mid peak
        (8, 1, float(bands.mid_freq), _db_to_linear(float(bands.mid_gain)), q_to_bandwidth(float(bands.q)), 0),
        # high shelf
        (1, 1, float(bands.high_freq), _db_to_linear(float(bands.high_gain)), 2.0, 0),
    ]

    stride = 33
    suffix = struct.pack("<iid", 1, 1, 1.0)
    chunk_size = 8 + (len(filters) * stride) + len(suffix)

    data = bytearray(REAEQ_SIGNATURE)
    data += struct.pack("<I", chunk_size)
    data += REAEQ_SUBHEADER
    data += struct.pack("<II", stride, len(filters))
    for band_type, enabled, freq, gain_lin, bandwidth, flag in filters:
        data += struct.pack("<iidddB", band_type, enabled, freq, gain_lin, bandwidth, flag)
    data += suffix

    b64 = base64.b64encode(data).decode("ascii")
    return "\n".join(b64[i:i + 128] for i in range(0, len(b64), 128))


def reaeq_fxchunk(bands):
    state = build_reaeq_state(bands)
    lines = ['<VST "VST: ReaEQ (Cockos)" reaeq.dll 0 "" 1919247729']
    lines.extend(state.splitlines())
    lines.append(">")
    return lines


# ---------------------------------------------------------------------------
# RPP emission / CLI
# ---------------------------------------------------------------------------

def _quote_rpp(value):
    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    return '"' + text.replace('"', '\\"') + '"'


def _fmt_seconds(samples, sample_rate):
    if sample_rate <= 0:
        return "0.000000"
    return f"{(float(samples) / float(sample_rate)):.6f}"


def _fmt_gain(value):
    return f"{float(value):.9g}"


def _decode_header(chunks):
    sample_rate = 44100
    master_left = 1.0
    master_right = 1.0
    entry = chunks.get("hdr ")
    if entry is None:
        return sample_rate, master_left, master_right
    body = entry[0]
    try:
        sample_rate = int(u32(body, 0))
    except ValueError:
        sample_rate = 44100
    try:
        master_left = float(f64(body, 16))
        master_right = float(f64(body, 24))
    except ValueError:
        master_left = 1.0
        master_right = 1.0
    return sample_rate, master_left, master_right


def resolve_media(path, ses_dir, media_dir):
    """Resolve a Windows source path to an existing local file.

    Preference: basename beside the .ses file, then basename in media_dir.
    Returns (resolved_path, found).
    """
    if not path:
        return path, False

    base = _basename(path)
    if ses_dir and base:
        candidate = os.path.join(ses_dir, base)
        if os.path.exists(candidate):
            return candidate, True

    if media_dir and base:
        candidate = os.path.join(media_dir, base)
        if os.path.exists(candidate):
            return candidate, True

    return path, False


def _source_type(path):
    ext = os.path.splitext(path.lower())[1]
    if ext == ".mp3":
        return "MP3"
    return "WAVE"


def _to_project_relative(path, project_dir):
    """Render a media path relative to the project directory when possible."""
    if not path:
        return path
    if not project_dir:
        return path
    try:
        rel = os.path.relpath(path, project_dir)
    except Exception:
        return path
    if rel.startswith("./"):
        rel = rel[2:]
    return rel.replace(os.sep, "/")


def build_rpp(session, media_dir=None, eq_enabled=True, master_enabled=True):
    warnings = []
    sample_rate = max(int(session.sample_rate), 1)
    ses_dir = os.path.dirname(os.path.abspath(session.ses_path)) if session.ses_path else ""
    tempo_bpm, tempo_num, tempo_den = session.tempo

    master_vol, master_pan = gains_to_volpan(session.master_left, session.master_right)
    if not master_enabled:
        master_vol, master_pan = 1.0, 0.0

    lines = [
        '<REAPER_PROJECT 0.1 "7.0/x64" 0',
        f"  SAMPLERATE {sample_rate} 0 0",
        "  PANLAW 1",
        "  PANLAWFLAGS 3",
        "  PANMODE 3",
        f"  TEMPO {float(tempo_bpm):.6f} {int(tempo_num)} {int(tempo_den)}",
        f"  MASTER_VOLUME {_fmt_gain(master_vol)} {_fmt_gain(master_pan)} -1 -1 1",
    ]

    clip_lists = {}
    for clip in session.clips:
        clip_lists.setdefault(clip.track, []).append(clip)

    for track in session.tracks:
        guid = "{" + str(uuid.uuid4()).upper() + "}"
        vol, pan = gains_to_volpan(track.left, track.right)
        lines.append(f"  <TRACK {guid}")
        lines.append(f"    NAME {_quote_rpp(track.name)}")
        lines.append(f"    VOLPAN {_fmt_gain(vol)} {_fmt_gain(pan)} -1 -1 1")
        lines.append(f"    MUTESOLO {1 if track.mute else 0} {1 if track.solo else 0} 0")
        lines.append("    NCHAN 2")

        if eq_enabled and track.index in session.eq:
            lines.append("    <FXCHAIN")
            lines.append("      SHOW 0")
            for fx_line in reaeq_fxchunk(session.eq[track.index]):
                lines.append(f"      {fx_line}")
            lines.append("    >")

        for clip in clip_lists.get(track.index, []):
            clip_cfg = session.clip_gain.get(clip.index, {})
            item_vol = float(clip_cfg.get("volume", clip.gain))
            item_pan = 0.0
            if "pan_points" in clip_cfg and clip_cfg["pan_points"]:
                # Cool Edit pan envelope uses 0.5 as center.
                item_pan = (clip_cfg["pan_points"][0][1] * 2.0) - 1.0

            resolved = clip.path
            found = False
            if clip.path:
                resolved, found = resolve_media(clip.path, ses_dir, media_dir)
                if not found:
                    warnings.append(
                        f"unresolved media: {clip.path} (track {track.index}, clip {clip.index})"
                    )

            src_path = resolved or (clip.path or "")
            if found:
                src_path = _to_project_relative(src_path, ses_dir)
            source_type = _source_type(src_path)
            loop_flag = 1 if clip.index in session.loops else 0

            lines.append("    <ITEM")
            lines.append(f"      POSITION {_fmt_seconds(clip.start, sample_rate)}")
            lines.append(f"      LENGTH {_fmt_seconds(clip.length, sample_rate)}")
            lines.append(f"      SOFFS {_fmt_seconds(clip.source_in, sample_rate)}")
            lines.append(f"      LOOP {loop_flag}")
            lines.append(f"      NAME {_quote_rpp(clip.name)}")
            lines.append(f"      VOLPAN {_fmt_gain(item_vol)} {_fmt_gain(item_pan)} 1 -1")
            lines.append("      MUTE 0 0")
            lines.append(f"      <SOURCE {source_type}")
            lines.append(f"        FILE {_quote_rpp(src_path)} 1")
            lines.append("      >")
            lines.append("    >")

        lines.append("  >")

    lines.append(">")
    return "\n".join(lines) + "\n", warnings


def convert_file(ses_path, out_path=None, media_dir=None, **opts):
    strict = bool(opts.get("strict", False))
    debug = bool(opts.get("debug", False))
    eq_enabled = bool(opts.get("eq_enabled", True))
    master_enabled = bool(opts.get("master_enabled", True))

    with open(ses_path, "rb") as fh:
        data = fh.read()

    chunks = walk_chunks(data)
    sample_rate, master_left, master_right = _decode_header(chunks)
    media = decode_media(data, chunks)
    tracks = decode_tracks(chunks)
    clips = decode_clips(chunks, media)
    tempo = decode_tempo(chunks)
    cues = decode_cues(chunks)
    loops = decode_loops(chunks, clips)
    clip_gain = decode_clip_gain(chunks, clips)
    eq = decode_eq(chunks)

    if debug:
        print(
            "debug: "
            f"chunks={len(chunks)} tracks={len(tracks)} clips={len(clips)} "
            f"media={len(media)} cues={len(cues)} eq={len(eq)}",
            file=sys.stderr,
        )

    session = Session(
        ses_path=ses_path,
        sample_rate=sample_rate,
        tempo=tempo,
        tracks=tracks,
        clips=clips,
        cues=cues,
        loops=loops,
        clip_gain=clip_gain,
        eq=eq,
        master_left=master_left,
        master_right=master_right,
    )

    text, warnings = build_rpp(
        session,
        media_dir=media_dir,
        eq_enabled=eq_enabled,
        master_enabled=master_enabled,
    )

    unresolved = [w for w in warnings if w.startswith("unresolved media:")]
    if strict and unresolved:
        raise RuntimeError(f"strict mode: {len(unresolved)} missing media file(s)")

    if out_path is None:
        stem, _ = os.path.splitext(ses_path)
        out_path = stem + ".rpp"

    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return out_path, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert Cool Edit .ses sessions to REAPER .rpp")
    parser.add_argument("ses", nargs="+", help="input .ses file(s)")
    parser.add_argument("-o", "--out", help="output .rpp path (single input only)")
    parser.add_argument("--media-dir", help="optional directory to search for media basenames")
    parser.add_argument("--strict", action="store_true", help="fail if any media path cannot be resolved")
    parser.add_argument("--no-eq", action="store_true", help="disable ReaEQ emission")
    parser.add_argument("--no-master", action="store_true", help="do not map hdr gains to master volume")
    parser.add_argument("--debug", action="store_true", help="print decoded counts")

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse usage error
        return int(exc.code)

    if args.out and len(args.ses) != 1:
        print("error: --out can only be used with a single input file", file=sys.stderr)
        return 1

    try:
        for ses_path in args.ses:
            out_path = args.out if len(args.ses) == 1 else None
            written, warnings = convert_file(
                ses_path,
                out_path=out_path,
                media_dir=args.media_dir,
                strict=args.strict,
                eq_enabled=not args.no_eq,
                master_enabled=not args.no_master,
                debug=args.debug,
            )
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            print(written)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
