import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("player", Path(__file__).parents[1] / "player.py")
player = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(player)


class PlayerHelpersTest(unittest.TestCase):
    def test_accepts_youtube_video_and_short_url(self):
        self.assertTrue(player.valid_video_url("https://www.youtube.com/watch?v=abc123"))
        self.assertTrue(player.valid_video_url("https://youtu.be/abc123"))

    def test_rejects_non_youtube_url(self):
        self.assertFalse(player.valid_video_url("https://example.com/video"))
        self.assertFalse(player.valid_video_url("javascript:alert(1)"))

    def test_formats_durations(self):
        self.assertEqual(player.format_duration(65), "1:05")
        self.assertEqual(player.format_duration(3661), "1:01:01")
        self.assertEqual(player.format_duration(None), "")


if __name__ == "__main__":
    unittest.main()
