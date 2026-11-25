import os
import tempfile
import unittest

from ses2rpp import Clip, Session, Track, build_rpp, resolve_media


class RppBuildTests(unittest.TestCase):
    def test_build_rpp_one_track_one_item(self):
        session = Session(
            ses_path="/tmp/demo/Untitled.ses",
            sample_rate=44100,
            tempo=(100.0, 4, 4),
            tracks=[Track(1, "Track 1", 1.0, 1.0, False, False)],
            clips=[
                Clip(
                    index=1,
                    track=1,
                    start=22050,
                    length=44100,
                    handle=111,
                    source_in=11025,
                    path=r"C:\Sessions\Song\Track 2 (4).mp3",
                    name="Track 2 (4).mp3",
                    gain=1.0,
                )
            ],
            cues=[],
            loops={},
            clip_gain={},
            eq={},
            master_left=1.0,
            master_right=1.0,
        )

        text, warnings = build_rpp(session, media_dir=None, eq_enabled=False, master_enabled=True)
        self.assertIn("PANLAW 1", text)
        self.assertEqual(text.count("<TRACK"), 1)
        self.assertEqual(text.count("<ITEM"), 1)
        self.assertIn("POSITION 0.500000", text)
        self.assertIn("LENGTH 1.000000", text)
        self.assertIn("SOFFS 0.250000", text)
        self.assertIn('FILE "C:\\Sessions\\Song\\Track 2 (4).mp3" 1', text)
        self.assertEqual(len(warnings), 1)


class ResolveMediaTests(unittest.TestCase):
    def test_resolve_media_prefers_ses_dir_then_media_dir(self):
        win_path = r"C:\Old\Path\Track.wav"
        with tempfile.TemporaryDirectory() as ses_dir, tempfile.TemporaryDirectory() as media_dir:
            ses_hit = os.path.join(ses_dir, "Track.wav")
            media_hit = os.path.join(media_dir, "Track.wav")

            open(ses_hit, "wb").close()
            resolved, ok = resolve_media(win_path, ses_dir, media_dir)
            self.assertTrue(ok)
            self.assertEqual(resolved, ses_hit)

            os.remove(ses_hit)
            open(media_hit, "wb").close()
            resolved, ok = resolve_media(win_path, ses_dir, media_dir)
            self.assertTrue(ok)
            self.assertEqual(resolved, media_hit)

            os.remove(media_hit)
            resolved, ok = resolve_media(win_path, ses_dir, media_dir)
            self.assertFalse(ok)
            self.assertEqual(resolved, win_path)


class RelativePathEmissionTests(unittest.TestCase):
    def test_build_rpp_uses_relative_source_path_when_media_is_found(self):
        with tempfile.TemporaryDirectory() as td:
            ses_path = os.path.join(td, "Untitled.ses")
            local_media = os.path.join(td, "Track 2 (4).mp3")
            open(local_media, "wb").close()

            session = Session(
                ses_path=ses_path,
                sample_rate=44100,
                tempo=(100.0, 4, 4),
                tracks=[Track(1, "Track 1", 1.0, 1.0, False, False)],
                clips=[
                    Clip(
                        index=1,
                        track=1,
                        start=0,
                        length=44100,
                        handle=111,
                        source_in=0,
                        path=r"C:\Sessions\Song\Track 2 (4).mp3",
                        name="Track 2 (4).mp3",
                        gain=1.0,
                    )
                ],
                cues=[],
                loops={},
                clip_gain={},
                eq={},
                master_left=1.0,
                master_right=1.0,
            )

            text, warnings = build_rpp(session, media_dir=None, eq_enabled=False, master_enabled=True)
            self.assertIn('FILE "Track 2 (4).mp3" 1', text)
            self.assertNotIn(local_media, text)
            self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
