"""Shared test setup: a fresh app with an in-memory database, a temp upload folder and a fake Meshy."""
import io
import os
import shutil
import tempfile
import unittest
import uuid
from unittest.mock import patch

from app import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"fake png body"
JPG = b"\xff\xd8\xff\xe0" + b"fake jpg body"


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.upload_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.upload_dir, ignore_errors=True)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "UPLOAD_DIR": self.upload_dir,
            "USE_MOCK_AI": False,
        })
        self.client = self.app.test_client()
        self.meshy_calls = []
        # Fake only the external Meshy boundary; everything else is the real app.
        for name, fake in [("generate_image", self.fake_image), ("refine_image", self.fake_refine),
                           ("generate_sculpture", self.fake_sculpture)]:
            p = patch(f"app.services.meshy_service.{name}", side_effect=fake)
            p.start()
            self.addCleanup(p.stop)

    # ── fake Meshy ──
    def write(self, extension):
        name = f"{uuid.uuid4()}{extension}"
        with open(os.path.join(self.upload_dir, name), "wb") as f:
            f.write(b"data")
        return name

    def fake_image(self, prompt):
        self.meshy_calls.append(("image", prompt))
        return {"image_url": self.write(".png"), "task_id": "t1"}

    def fake_refine(self, prompt, previous_path):
        self.meshy_calls.append(("refine", prompt, os.path.basename(previous_path)))
        return {"image_url": self.write(".png"), "task_id": "t2"}

    def fake_sculpture(self, image_path):
        self.meshy_calls.append(("sculpture", os.path.basename(image_path)))
        return {"sculpture_url": self.write(".glb"), "stl_url": self.write(".stl")}

    # ── API helpers ──
    def signup(self, email="ana@example.com", password="password123", client=None):
        return (client or self.client).post("/signup", data={"email": email, "password": password})

    def other_user_client(self):
        client = self.app.test_client()
        self.signup("bob@example.com", client=client)
        return client

    def post_json(self, url, body=None, client=None):
        return (client or self.client).post(url, json=body or {})

    def new_generation(self, client=None):
        return self.post_json("/api/generation", client=client).get_json()["generation_id"]

    def iterate(self, gen_id, prompt="a marble owl", client=None):
        return self.post_json(f"/api/generation/{gen_id}/iterate", {"prompt": prompt}, client=client)

    def sculpt(self, gen_id, iteration_id, client=None):
        return self.post_json(f"/api/generation/{gen_id}/sculpture", {"iteration_id": iteration_id}, client=client)

    def upload(self, gen_id, filename="dragon.png", content=PNG):
        return self.client.post(f"/api/generation/{gen_id}/upload",
                                data={"image": (io.BytesIO(content), filename)},
                                content_type="multipart/form-data")

    def sculpted_generation(self):
        gen_id = self.new_generation()
        iteration_id = self.iterate(gen_id).get_json()["iteration_id"]
        self.assertEqual(self.sculpt(gen_id, iteration_id).status_code, 200)
        return gen_id
