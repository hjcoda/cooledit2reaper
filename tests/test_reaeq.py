import base64
import math
import os
import struct
import unittest

from ses2rpp import (
    EqBands,
    build_reaeq_state,
    decode_eq,
    q_to_bandwidth,
    reaeq_fxchunk,
    walk_chunks,
)


REAL_EQ_FIXTURE = "/home/henryj/Music/Scared In Amsterdam/Untitled.ses"


def parse_reaeq_state(state_text):
    raw = "".join(state_text.split())
    data = base64.b64decode(raw)

    sig = data[:48]
    chunk_size = struct.unpack_from("<I", data, 48)[0]
    sub_header = data[52:60]
    stride = struct.unpack_from("<I", data, 60)[0]
    band_count = struct.unpack_from("<I", data, 64)[0]

    bands = []
    off = 68
    for _ in range(band_count):
        btype, enabled = struct.unpack_from("<ii", data, off)
        freq = struct.unpack_from("<d", data, off + 8)[0]
        gain = struct.unpack_from("<d", data, off + 16)[0]
        bw = struct.unpack_from("<d", data, off + 24)[0]
        flag = data[off + 32]
        bands.append(
            {
                "type": btype,
                "enabled": enabled,
                "freq": freq,
                "gain": gain,
                "bw": bw,
                "flag": flag,
            }
        )
        off += stride

    trailer = struct.unpack_from("<iid", data, off)
    return {
        "signature": sig,
        "chunk_size": chunk_size,
        "sub_header": sub_header,
        "stride": stride,
        "band_count": band_count,
        "bands": bands,
        "trailer": trailer,
        "total_size": len(data),
    }


class EqDecodeTests(unittest.TestCase):
    def test_decode_eq_from_real_fixture(self):
        if not os.path.exists(REAL_EQ_FIXTURE):
            self.skipTest("real session fixture missing")

        with open(REAL_EQ_FIXTURE, "rb") as fh:
            data = fh.read()
        eq = decode_eq(walk_chunks(data))

        self.assertEqual(set(eq), {8, 12, 13, 14})
        self.assertAlmostEqual(eq[8].low_gain, 6.2)
        self.assertAlmostEqual(eq[12].low_gain, 5.4)
        self.assertAlmostEqual(eq[13].low_gain, 10.2)
        self.assertAlmostEqual(eq[14].low_gain, 7.7)

        for track_num in (8, 12, 13, 14):
            bands = eq[track_num]
            self.assertAlmostEqual(bands.low_freq, 160.0)
            self.assertAlmostEqual(bands.mid_freq, 2500.0)
            self.assertAlmostEqual(bands.high_freq, 7000.0)
            self.assertAlmostEqual(bands.q, 0.33)


class ReaEqStateTests(unittest.TestCase):
    def test_q_to_bandwidth_known_value(self):
        self.assertAlmostEqual(q_to_bandwidth(0.33), 3.4715238530581938, places=6)

    def test_build_reaeq_state_round_trip(self):
        bands = EqBands(
            low_freq=160.0,
            low_gain=6.2,
            mid_freq=2500.0,
            mid_gain=0.0,
            q=0.33,
            high_freq=7000.0,
            high_gain=0.0,
        )

        state = build_reaeq_state(bands)
        parsed = parse_reaeq_state(state)

        self.assertEqual(parsed["sub_header"], b"\x01\x00\x00\x00\x00\x00\x10\x00")
        self.assertEqual(parsed["stride"], 33)
        self.assertEqual(parsed["band_count"], 3)
        self.assertEqual(parsed["chunk_size"], parsed["total_size"] - 60)
        self.assertEqual(parsed["trailer"], (1, 1, 1.0))

        got = parsed["bands"]
        self.assertEqual([b["type"] for b in got], [0, 8, 1])
        self.assertEqual([b["enabled"] for b in got], [1, 1, 1])
        self.assertAlmostEqual(got[0]["freq"], 160.0)
        self.assertAlmostEqual(got[1]["freq"], 2500.0)
        self.assertAlmostEqual(got[2]["freq"], 7000.0)
        self.assertAlmostEqual(got[0]["gain"], 10 ** (6.2 / 20), places=9)
        self.assertAlmostEqual(got[1]["gain"], 1.0, places=9)
        self.assertAlmostEqual(got[2]["gain"], 1.0, places=9)
        self.assertAlmostEqual(got[0]["bw"], 2.0, places=9)
        self.assertAlmostEqual(got[1]["bw"], q_to_bandwidth(0.33), places=9)
        self.assertAlmostEqual(got[2]["bw"], 2.0, places=9)

        for line in state.splitlines():
            self.assertLessEqual(len(line), 128)

    def test_reaeq_fxchunk_shape(self):
        bands = EqBands(160.0, 6.2, 2500.0, 0.0, 0.33, 7000.0, 0.0)
        lines = reaeq_fxchunk(bands)
        self.assertTrue(lines[0].startswith('<VST "VST: ReaEQ (Cockos)"'))
        self.assertEqual(lines[-1], ">")


if __name__ == "__main__":
    unittest.main()
