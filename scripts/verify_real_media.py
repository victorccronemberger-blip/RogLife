"""Exercise the existing MP4 contracts with this checkout's local FFmpeg."""
from pathlib import Path
import importlib.util
import unittest


def test_bundled_media_contracts():
    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "local_real_timestamps", project / "test/test_real_video_timestamps.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.FFMPEG.is_file() and module.FFPROBE.is_file()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.RealVideoTimestampsTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert result.testsRun == 5 and not result.skipped and result.wasSuccessful()


if __name__ == "__main__":
    test_bundled_media_contracts()
