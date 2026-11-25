import struct
import unittest

from ses2rpp import (
    Clip,
    decode_clip_gain,
    decode_clips,
    decode_cues,
    decode_loops,
    decode_tempo,
    decode_tracks,
    gains_to_volpan,
    walk_chunks,
)
from tests.helpers import make_chunk, make_ses


def p32(value):
    return struct.pack("<I", value)


def pi32(value):
    return struct.pack("<i", value)


def pf64(value):
    return struct.pack("<d", value)


def _name40(text):
    raw = text.encode("latin1")[:39]
    return raw + (b"\x00" * (40 - len(raw)))


def make_cep2_track(left, right, flags, name):
    rec = bytearray(96)
    rec[0:8] = pf64(left)
    rec[8:16] = pf64(right)
    rec[16:20] = p32(flags)
    rec[20:60] = _name40(name)
    return bytes(rec)


def make_audition_track(volume, mute, name):
    rec = bytearray(152)
    rec[4:12] = pf64(volume)
    rec[20:24] = pi32(1 if mute else 0)
    rec[24:64] = _name40(name)
    return bytes(rec)


def make_blk_record(*, gain, start, length, handle, pointer, track, source_in):
    rec = bytearray(88)
    rec[0:8] = pf64(gain)
    rec[8:16] = pf64(1.0)
    rec[28:32] = pi32(start)
    rec[32:36] = pi32(length)
    rec[36:40] = p32(handle)
    rec[44:48] = pi32(pointer)
    rec[48:52] = pi32(track)
    rec[60:64] = pi32(source_in)
    return bytes(rec)


def make_blk2(handles):
    body = bytearray()
    body += p32(len(handles))
    for handle in handles:
        rec = bytearray(40)
        rec[0:4] = p32(handle)
        body += rec
    return bytes(body)


def make_ep20(records):
    """Build ep20 body.

    records = [{"handle": int, "groups": [{"pid": int, "points": [(sample, value), ...]}]}]
    """
    body = bytearray()
    body += p32(len(records))
    for rec in records:
        groups = rec["groups"]
        body += p32(0x10)  # marker
        body += p32(12)    # version
        body += p32(rec["handle"])
        body += p32(len(groups))
        for group in groups:
            points = group["points"]
            body += p32(len(points))
            body += p32(group["pid"])
            body += p32(0)
            for sample, value in points:
                body += p32(sample)
                body += pf64(value)
    return bytes(body)


def make_bk20(handles_with_extent):
    recsize = 72
    body = bytearray()
    body += p32(len(handles_with_extent))
    body += p32(recsize)
    for handle, extent in handles_with_extent:
        rec = bytearray(recsize)
        rec[0:4] = p32(handle)
        rec[28:32] = pi32(extent)
        body += rec
    return bytes(body)


class TrackDecodeTests(unittest.TestCase):
    def test_decode_tracks_cep2_layout(self):
        body = p32(2)
        body += make_cep2_track(1.0, 1.0, 0, "Lead Vox")
        body += make_cep2_track(0.5, 0.0, 4, "Guitar L")
        chunks = walk_chunks(make_ses(make_chunk(b"trks", body)))

        tracks = decode_tracks(chunks)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0].name, "Lead Vox")
        self.assertEqual((tracks[0].left, tracks[0].right), (1.0, 1.0))
        self.assertEqual((tracks[0].mute, tracks[0].solo), (False, False))
        self.assertEqual(tracks[1].name, "Guitar L")
        self.assertAlmostEqual(tracks[1].left, 0.5)
        self.assertAlmostEqual(tracks[1].right, 0.0)

    def test_decode_tracks_audition_layout(self):
        body = make_audition_track(0.7, True, "Muted")
        body += make_audition_track(1.1, False, "Open")
        chunks = walk_chunks(make_ses(make_chunk(b"trks", body)))

        tracks = decode_tracks(chunks)
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0].name, "Muted")
        self.assertTrue(tracks[0].mute)
        self.assertAlmostEqual(tracks[0].left, 0.7)
        self.assertAlmostEqual(tracks[0].right, 0.7)
        self.assertFalse(tracks[1].mute)

    def test_decode_tracks_unknown_layout_is_empty(self):
        chunks = walk_chunks(make_ses(make_chunk(b"trks", b"\x00" * 17)))
        self.assertEqual(decode_tracks(chunks), [])


class ClipDecodeTests(unittest.TestCase):
    def test_decode_clips_reads_blk_records(self):
        body = bytearray()
        body += p32(2)
        body += p32(0)
        body += make_blk_record(
            gain=1.0,
            start=22050,
            length=44100,
            handle=111,
            pointer=501,
            track=3,
            source_in=64,
        )
        body += make_blk_record(
            gain=0.8,
            start=0,
            length=88200,
            handle=222,
            pointer=777,
            track=1,
            source_in=0,
        )
        chunks = walk_chunks(make_ses(make_chunk(b"blk ", bytes(body))))
        media = {501: r"C:\Sessions\vox.wav", 777: r"C:\Sessions\drums.mp3"}

        clips = decode_clips(chunks, media)
        self.assertEqual(len(clips), 2)
        self.assertEqual((clips[0].index, clips[0].track), (1, 3))
        self.assertEqual((clips[0].start, clips[0].length), (22050, 44100))
        self.assertEqual((clips[0].source_in, clips[0].handle), (64, 111))
        self.assertEqual(clips[0].path, r"C:\Sessions\vox.wav")
        self.assertEqual((clips[1].index, clips[1].track), (2, 1))
        self.assertAlmostEqual(clips[1].gain, 0.8)


class MappingDecodeTests(unittest.TestCase):
    def test_gains_to_volpan_cases(self):
        self.assertEqual(gains_to_volpan(1.0, 1.0), (1.0, 0.0))
        self.assertEqual(gains_to_volpan(0.5, 0.0), (0.5, -1.0))
        self.assertEqual(gains_to_volpan(0.0, 0.5), (0.5, 1.0))
        vol, pan = gains_to_volpan(0.75, 1.0)
        self.assertAlmostEqual(vol, 1.0)
        self.assertAlmostEqual(pan, 0.25)

    def test_decode_tempo_defaults_and_values(self):
        self.assertEqual(decode_tempo({}), (120.0, 4, 4))
        tempo_body = pf64(100.0) + p32(4) + p32(16)
        chunks = walk_chunks(make_ses(make_chunk(b"tmpo", tempo_body)))
        self.assertEqual(decode_tempo(chunks), (100.0, 4, 4))

    def test_decode_cues_reads_records(self):
        body = bytearray()
        body += p32(2)
        rec1 = bytearray(24)
        rec1[0:4] = pi32(44100)
        rec1[4:8] = pi32(0)
        rec1[12:24] = b"Verse\x00\x00\x00\x00\x00\x00\x00"
        rec2 = bytearray(24)
        rec2[0:4] = pi32(88200)
        rec2[4:8] = pi32(22050)
        rec2[12:24] = b"Chorus\x00\x00\x00\x00\x00\x00"
        body += rec1 + rec2
        chunks = walk_chunks(make_ses(make_chunk(b"cues", bytes(body))))

        self.assertEqual(
            decode_cues(chunks),
            [(44100, 0, "Verse"), (88200, 22050, "Chorus")],
        )

    def test_decode_loops_defaults_and_extent_map(self):
        clips = [
            Clip(1, 1, 0, 100, 111, 0, None, "a", 1.0),
            Clip(2, 1, 0, 100, 222, 0, None, "b", 1.0),
        ]
        self.assertEqual(decode_loops({}, clips), {})

        loop_body = p32(1) + p32(222)
        bk20_body = make_bk20([(111, 777), (222, 888)])
        chunks = walk_chunks(
            make_ses(
                make_chunk(b"loop", loop_body),
                make_chunk(b"bk20", bk20_body),
            )
        )
        self.assertEqual(decode_loops(chunks, clips), {2: {"extent": 888}})

    def test_decode_clip_gain_identity_ep20_returns_empty(self):
        clips = [Clip(1, 1, 0, 1000, 111, 0, None, "a", 1.0)]
        ep20_body = make_ep20(
            [
                {
                    "handle": 111,
                    "groups": [
                        {"pid": 1, "points": [(0, 1.0), (999, 1.0)]},
                        {"pid": 2, "points": [(0, 0.5), (999, 0.5)]},
                    ],
                }
            ]
        )
        chunks = walk_chunks(
            make_ses(
                make_chunk(b"blk2", make_blk2([111])),
                make_chunk(b"ep20", ep20_body),
            )
        )
        self.assertEqual(decode_clip_gain(chunks, clips), {})

    def test_decode_clip_gain_non_identity_envelope(self):
        clips = [Clip(1, 1, 0, 1000, 111, 0, None, "a", 1.0)]
        ep20_body = make_ep20(
            [
                {
                    "handle": 111,
                    "groups": [
                        {"pid": 1, "points": [(0, 1.0), (1000, 0.5)]},
                        {"pid": 2, "points": [(0, 0.5), (1000, 0.5)]},
                    ],
                }
            ]
        )
        chunks = walk_chunks(
            make_ses(
                make_chunk(b"blk2", make_blk2([111])),
                make_chunk(b"ep20", ep20_body),
            )
        )

        out = decode_clip_gain(chunks, clips)
        self.assertIn(1, out)
        self.assertIn("volume_points", out[1])
        self.assertEqual(out[1]["volume_points"], [(0, 1.0), (1000, 0.5)])


if __name__ == "__main__":
    unittest.main()
