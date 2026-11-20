import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from app.services import meshy_service
from app.services.meshy_service import MeshyError, image_to_base64, poll_task


class ImageToBase64Tests(unittest.TestCase):
    def encode(self, suffix):
        handle, path = tempfile.mkstemp(suffix=suffix)
        os.write(handle, b"abc")
        os.close(handle)
        try:
            return image_to_base64(path)
        finally:
            os.remove(path)

    def test_jpg_is_labelled_jpeg(self):
        self.assertTrue(self.encode(".jpg").startswith("data:image/jpeg;base64,"))

    def test_png_is_labelled_png(self):
        self.assertEqual(self.encode(".png"), "data:image/png;base64,YWJj")


class PollTaskTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config["MESHY_API_KEY"] = "k"
        ctx = app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)

    def respond(self, *statuses):
        responses = [MagicMock(ok=True, json=MagicMock(return_value={"status": s, "task_error": {"message": "bad"}}))
                     for s in statuses]
        return patch.object(meshy_service.requests, "request", side_effect=responses)

    def test_returns_task_once_it_succeeds(self):
        with self.respond("PENDING", "SUCCEEDED") as request:
            self.assertEqual(poll_task("text-to-image", "t", interval=0)["status"], "SUCCEEDED")
        self.assertEqual(request.call_args.kwargs["timeout"], meshy_service.API_TIMEOUT)

    def test_survives_a_brief_network_blip(self):
        blip = MagicMock(ok=False, status_code=502, text="bad gateway")
        done = MagicMock(ok=True, json=MagicMock(return_value={"status": "SUCCEEDED"}))
        with patch.object(meshy_service.requests, "request", side_effect=[blip, blip, done]):
            self.assertEqual(poll_task("image-to-3d", "t", interval=0)["status"], "SUCCEEDED")

    def test_gives_up_after_repeated_failures(self):
        blip = MagicMock(ok=False, status_code=502, text="bad gateway")
        with patch.object(meshy_service.requests, "request", return_value=blip), self.assertRaises(MeshyError):
            poll_task("image-to-3d", "t", interval=0)

    def test_non_json_reply_is_a_meshy_error(self):
        html = MagicMock(ok=True, text="<html>", json=MagicMock(side_effect=ValueError("no json")))
        with patch.object(meshy_service.requests, "request", return_value=html), self.assertRaises(MeshyError):
            poll_task("image-to-3d", "t", interval=0)

    def test_failed_task_raises(self):
        with self.respond("FAILED"), self.assertRaisesRegex(MeshyError, "bad"):
            poll_task("text-to-image", "t", interval=0)

    def test_gives_up_after_timeout(self):
        with self.respond(*["PENDING"] * 50), self.assertRaisesRegex(MeshyError, "timed out"):
            poll_task("text-to-image", "t", interval=0.01, timeout=0.05)


if __name__ == "__main__":
    unittest.main()
