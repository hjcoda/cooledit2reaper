import os
import unittest

from ses2rpp import (
    convert_file,
    decode_clips,
    decode_media,
    decode_tempo,
    decode_tracks,
    u32,
    walk_chunks,
)


REAL_CASES = [
    ("/home/henryj/Music/Scared In Amsterdam/Untitled.ses", 23, 44),
    ("/home/henryj/Music/oncve again/Untitled.ses", 12, 17),
    ("/home/henryj/Music/My muse/Untitled.ses", 19, 20),
]


class RealSessionsIntegrationTests(unittest.TestCase):
    def test_real_sessions_convert_and_match_counts(self):
        missing = [path for path, _, _ in REAL_CASES if not os.path.exists(path)]
        if missing:
            self.skipTest(f"real session fixture(s) missing: {missing}")

        for ses_path, expected_tracks, expected_clips in REAL_CASES:
            with self.subTest(ses=ses_path):
                with open(ses_path, "rb") as fh:
                    data = fh.read()
                chunks = walk_chunks(data)
                media = decode_media(data, chunks)
                tracks = decode_tracks(chunks)
                clips = decode_clips(chunks, media)
                tempo_bpm, _, _ = decode_tempo(chunks)

                self.assertEqual(len(tracks), expected_tracks)
                self.assertEqual(len(clips), expected_clips)
                self.assertEqual(u32(chunks["hdr "][0], 0), 44100)
                self.assertAlmostEqual(tempo_bpm, 100.0)

                out_path, warnings = convert_file(ses_path)
                expected_out = os.path.splitext(ses_path)[0] + ".rpp"
                self.assertEqual(out_path, expected_out)
                self.assertTrue(os.path.exists(out_path))
                self.assertEqual(len(warnings), 1)
                self.assertIn("unresolved media:", warnings[0])

                with open(out_path, "r", encoding="utf-8") as fh:
                    rpp = fh.read()
                self.assertEqual(rpp.count("<TRACK"), expected_tracks)
                self.assertEqual(rpp.count("<ITEM"), expected_clips)


if __name__ == "__main__":
    unittest.main()
