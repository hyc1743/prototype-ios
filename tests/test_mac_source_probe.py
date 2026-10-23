import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from mac_source_probe import download_parts, observations


class SourceProbeTests(unittest.TestCase):
    def test_resource_download_validates_size_and_hash_before_publishing(self):
        data = b"verified resource fixture"
        descriptor = {"name": "PrototypeData.part001.ptdata", "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()}
        for downloaded in (data, data[:-1], data + b"extra", b"x" * len(data)):
            with self.subTest(downloaded=downloaded), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch("mac_source_probe.urllib.request.urlopen", return_value=io.BytesIO(downloaded)):
                    if downloaded == data:
                        download_parts(root, {"parts": [descriptor]})
                        self.assertEqual((root / descriptor["name"]).read_bytes(), data)
                    else:
                        with self.assertRaises(ValueError):
                            download_parts(root, {"parts": [descriptor]})
                        self.assertFalse((root / descriptor["name"]).exists())
                self.assertFalse((root / (descriptor["name"] + ".partial")).exists())

    def test_verified_existing_resource_is_reused_without_network(self):
        data = b"verified fixture"
        descriptor = {"name": "PrototypeData.part001.ptdata", "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / descriptor["name"]).write_bytes(data)
            with patch("mac_source_probe.urllib.request.urlopen") as download:
                download_parts(root, {"parts": [descriptor]})
                download.assert_not_called()

    def test_main_entry_and_input_do_not_establish_rendering_or_playability(self):
        log = ("[prototype-mac-probe] entering original source main\n"
               "[prototype-mac-probe] input button=6 ms=8000\n")
        result = observations(log)
        self.assertTrue(result["source_main_entered"])
        self.assertEqual(result["scripted_input_events"], 1)
        self.assertFalse(result["frontbuffer_log_observed"])
        self.assertFalse(result["scene_frame_diagnostic_observed"])
        self.assertNotIn("playable_verified", result)
        result = observations(log + "[native] Frontbuffer ABC\n[native] Pipeline creation failed VS\n")
        self.assertTrue(result["frontbuffer_log_observed"])
        self.assertEqual(len(result["pipeline_failure_lines"]), 1)


if __name__ == "__main__":
    unittest.main()
