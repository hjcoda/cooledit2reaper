import io
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from ses2rpp import main
from tests.helpers import make_chunk, make_listfile, make_ses


def p32(value):
    return struct.pack("<I", value)


def pi32(value):
    return struct.pack("<i", value)


def pf64(value):
    return struct.pack("<d", value)


def make_minimal_ses_bytes(source_path):
    hdr = bytearray(32)
    hdr[0:4] = p32(44100)
    hdr[16:24] = pf64(1.0)
    hdr[24:32] = pf64(1.0)

    tmpo = pf64(100.0) + p32(4) + p32(16)

    trk = bytearray(96)
    trk[0:8] = pf64(1.0)
    trk[8:16] = pf64(1.0)
    trk[20:27] = b"Track 1"
    trks = p32(1) + bytes(trk)

    rec = bytearray(88)
    rec[0:8] = pf64(1.0)
    rec[8:16] = pf64(1.0)
    rec[28:32] = pi32(22050)   # POSITION = 0.5 sec
    rec[32:36] = pi32(44100)   # LENGTH = 1.0 sec
    rec[36:40] = p32(1234)     # handle
    rec[44:48] = pi32(55)      # media pointer
    rec[48:52] = pi32(1)       # track index
    rec[60:64] = pi32(11025)   # SOFFS = 0.25 sec
    blk = p32(1) + p32(0) + bytes(rec)

    return make_ses(
        make_chunk(b"hdr ", bytes(hdr)),
        make_chunk(b"tmpo", tmpo),
        make_chunk(b"trks", trks),
        make_listfile([(55, source_path)]),
        make_chunk(b"blk ", blk),
    )


class CliTests(unittest.TestCase):
    def test_cli_writes_default_output_beside_input(self):
        with tempfile.TemporaryDirectory() as td:
            ses_path = os.path.join(td, "Untitled.ses")
            media_path = os.path.join(td, "Track 2 (4).mp3")
            open(media_path, "wb").close()

            ses_bytes = make_minimal_ses_bytes(r"C:\Legacy\Track 2 (4).mp3")
            with open(ses_path, "wb") as fh:
                fh.write(ses_bytes)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = main([ses_path])

            out_path = os.path.join(td, "Untitled.rpp")
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(out_path))
            self.assertIn(out_path, stdout.getvalue())

    def test_cli_strict_fails_on_missing_media(self):
        with tempfile.TemporaryDirectory() as td:
            ses_path = os.path.join(td, "Untitled.ses")
            with open(ses_path, "wb") as fh:
                fh.write(make_minimal_ses_bytes(r"C:\Missing\lost.wav"))

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = main(["--strict", ses_path])

            self.assertEqual(rc, 1)
            self.assertFalse(os.path.exists(os.path.join(td, "Untitled.rpp")))
            self.assertIn("error:", stderr.getvalue())

    def test_cli_non_coolness_input_is_clean_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad_path = os.path.join(td, "bad.ses")
            with open(bad_path, "wb") as fh:
                fh.write(b"not-coolness")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = main([bad_path])

            self.assertEqual(rc, 1)
            self.assertIn("error:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
