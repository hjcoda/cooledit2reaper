import unittest

from ses2rpp import decode_media, walk_chunks
from tests.helpers import make_chunk, make_listfile, make_ses


class ChunkWalkerTests(unittest.TestCase):
    def test_walk_chunks_reads_simple_chunks(self):
        data = make_ses(
            make_chunk(b"hdr ", b"\x44\xac\x00\x00"),
            make_chunk(b"tmpo", b"\x00" * 32),
        )
        chunks = walk_chunks(data)
        self.assertEqual(chunks["hdr "][0][:4], b"\x44\xac\x00\x00")
        self.assertEqual(set(chunks), {"hdr ", "tmpo"})

    def test_walk_chunks_keeps_first_occurrence(self):
        data = make_ses(
            make_chunk(b"trks", b"first"),
            make_chunk(b"trks", b"second"),
        )
        self.assertEqual(walk_chunks(data)["trks"][0], b"first")

    def test_walk_chunks_stops_cleanly_on_truncation(self):
        data = make_ses(make_chunk(b"hdr ", b"\x00" * 4))[:-2]
        self.assertEqual(walk_chunks(data), {})

    def test_walk_chunks_ignores_trailing_garbage(self):
        data = make_ses(make_chunk(b"hdr ", b"\x00" * 4)) + b"\x00\x01"
        self.assertEqual(set(walk_chunks(data)), {"hdr "})


class MediaMapTests(unittest.TestCase):
    def test_decode_media_maps_pointer_to_windows_path(self):
        path = r"C:\Sessions\Song\Track 2 (4).mp3"
        data = make_ses(make_listfile([(123, path)]))
        self.assertEqual(decode_media(data, walk_chunks(data)), {123: path})

    def test_decode_media_handles_multiple_records(self):
        data = make_ses(
            make_listfile(
                [
                    (1, r"C:\Sessions\a.wav"),
                    (2, r"C:\Sessions\b.mp3"),
                ]
            )
        )
        self.assertEqual(
            decode_media(data, walk_chunks(data)),
            {1: r"C:\Sessions\a.wav", 2: r"C:\Sessions\b.mp3"},
        )

    def test_decode_media_returns_empty_without_listfile(self):
        data = make_ses(make_chunk(b"hdr ", b"\x00" * 4))
        self.assertEqual(decode_media(data, walk_chunks(data)), {})


if __name__ == "__main__":
    unittest.main()
