"""Run all tests with:  venv\\Scripts\\python.exe -m unittest"""
import os
import threading
import unittest
from unittest.mock import patch

from app.services.meshy_service import MeshyError
from tests.helpers import JPG, AppTestCase


class GenerationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    # ── iterations ──
    def test_first_prompt_names_the_generation(self):
        gen_id = self.new_generation()
        response = self.iterate(gen_id, "x" * 80)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["name"], "x" * 50)

    def test_later_prompts_refine_the_previous_image(self):
        gen_id = self.new_generation()
        first = self.iterate(gen_id, "an owl").get_json()
        self.iterate(gen_id, "make it bronze")
        self.assertEqual(self.meshy_calls[1][:2], ("refine", "make it bronze"))
        self.assertEqual(self.meshy_calls[1][2], first["image_url"].rsplit("/", 1)[1])

    def test_blank_prompt_is_rejected_without_calling_meshy(self):
        gen_id = self.new_generation()
        for body in [{"prompt": "   "}, {}, {"prompt": 5}]:
            response = self.post_json(f"/api/generation/{gen_id}/iterate", body)
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.get_json())
        self.assertEqual(self.meshy_calls, [])

    def test_fourth_image_is_refused(self):
        gen_id = self.new_generation()
        for _ in range(3):
            self.iterate(gen_id)
        response = self.iterate(gen_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "Image generation limit reached.")

    def test_api_writes_must_be_json(self):
        response = self.client.post("/api/generation", data="x", content_type="text/plain")
        self.assertEqual(response.status_code, 415)

    # ── sculptures ──
    def test_sculpture_returns_file_urls_not_server_paths(self):
        gen_id = self.new_generation()
        iteration = self.iterate(gen_id).get_json()
        data = self.sculpt(gen_id, iteration["iteration_id"]).get_json()
        self.assertTrue(data["sculpture_url"].startswith("/files/") and data["sculpture_url"].endswith(".glb"))
        self.assertTrue(data["stl_url"].endswith(".stl"))
        self.assertEqual(data["source_image_url"], iteration["image_url"])
        self.assertNotIn(self.upload_dir, str(data))

    def test_second_sculpture_on_same_generation_is_refused(self):
        gen_id = self.new_generation()
        iteration_id = self.iterate(gen_id).get_json()["iteration_id"]
        self.sculpt(gen_id, iteration_id)
        self.assertEqual(self.sculpt(gen_id, iteration_id).status_code, 403)
        self.assertEqual(self.iterate(gen_id).status_code, 403)

    def test_sculpture_in_progress_blocks_a_second_request(self):
        gen_id = self.new_generation()
        iteration_id = self.iterate(gen_id).get_json()["iteration_id"]
        started, release = threading.Event(), threading.Event()

        def slow_sculpture(path):
            started.set()
            release.wait(5)
            return self.fake_sculpture(path)

        with patch("app.services.meshy_service.generate_sculpture", side_effect=slow_sculpture):
            first = threading.Thread(target=self.sculpt, args=(gen_id, iteration_id))
            first.start()
            started.wait(5)
            second = self.sculpt(gen_id, iteration_id)
            release.set()
            first.join()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(sum(c[0] == "sculpture" for c in self.meshy_calls), 1)

    def run_while_blocked(self, target, name, during):
        """Start `target` in a thread, hold the faked Meshy call `name` open, and run `during()` meanwhile."""
        started, release = threading.Event(), threading.Event()
        fake = getattr(self, {"generate_image": "fake_image", "generate_sculpture": "fake_sculpture"}[name])

        def slow(*args):
            started.set()
            release.wait(5)
            return fake(*args)

        with patch(f"app.services.meshy_service.{name}", side_effect=slow):
            worker = threading.Thread(target=target)
            worker.start()
            started.wait(5)
            result = during()
            release.set()
            worker.join()
        return result

    def test_image_in_progress_blocks_a_second_request(self):
        gen_id = self.new_generation()
        second = self.run_while_blocked(lambda: self.iterate(gen_id, "first"), "generate_image",
                                        lambda: self.iterate(gen_id, "second"))
        self.assertEqual(second.status_code, 409)
        self.assertEqual(len(self.client.get(f"/api/generation/{gen_id}").get_json()["iterations"]), 1)
        self.assertEqual(self.iterate(gen_id, "now allowed").status_code, 201)  # marker cleared afterwards

    def test_cannot_delete_while_sculpting(self):
        gen_id = self.new_generation()
        iteration_id = self.iterate(gen_id).get_json()["iteration_id"]
        response = self.run_while_blocked(lambda: self.sculpt(gen_id, iteration_id), "generate_sculpture",
                                          lambda: self.client.delete(f"/api/generation/{gen_id}", json={}))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.delete(f"/api/generation/{gen_id}", json={}).status_code, 200)
        self.assertEqual(os.listdir(self.upload_dir), [])

    def test_failed_image_releases_the_marker(self):
        gen_id = self.new_generation()
        with patch("app.services.meshy_service.generate_image", side_effect=MeshyError("down")):
            self.assertEqual(self.iterate(gen_id).status_code, 502)
        self.assertEqual(self.iterate(gen_id).status_code, 201)

    def test_daily_limit_is_three_sculptures(self):
        for _ in range(3):
            self.sculpted_generation()
        gen_id = self.new_generation()
        iteration_id = self.iterate(gen_id).get_json()["iteration_id"]
        response = self.sculpt(gen_id, iteration_id)
        self.assertEqual(response.status_code, 429)
        self.assertIn("limit", response.get_json()["error"])

    def test_failed_sculptures_do_not_count(self):
        gen_id = self.new_generation()
        iteration_id = self.iterate(gen_id).get_json()["iteration_id"]
        with patch("app.services.meshy_service.generate_sculpture", side_effect=MeshyError("down")):
            for _ in range(3):
                self.assertEqual(self.sculpt(gen_id, iteration_id).status_code, 502)
        self.assertEqual(self.sculpt(gen_id, iteration_id).status_code, 200)

    # ── upload ──
    def test_upload_goes_straight_to_3d_under_a_random_name(self):
        gen_id = self.new_generation()
        response = self.upload(gen_id, "../my dragon.jpg", JPG)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["name"], "my dragon")
        self.assertEqual(data["iterations"], [])
        self.assertTrue(data["source_image_url"].endswith(".jpg"))
        self.assertNotIn("dragon", data["source_image_url"])

    def test_upload_rejects_other_file_types(self):
        gen_id = self.new_generation()
        self.assertEqual(self.upload(gen_id, "notes.txt").status_code, 400)
        self.assertEqual(self.upload(gen_id, "fake.png", b"not really a png").status_code, 400)

    def test_upload_rejects_files_over_10mb(self):
        gen_id = self.new_generation()
        response = self.upload(gen_id, "big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (10 * 1024 * 1024))
        self.assertEqual(response.status_code, 413)
        self.assertIn("error", response.get_json())

    def test_upload_only_on_an_empty_generation(self):
        gen_id = self.new_generation()
        self.iterate(gen_id)
        self.assertEqual(self.upload(gen_id).status_code, 409)

    def test_failed_upload_leaves_no_file_behind(self):
        gen_id = self.new_generation()
        with patch("app.services.meshy_service.generate_sculpture", side_effect=MeshyError("down")):
            self.assertEqual(self.upload(gen_id).status_code, 502)
        self.assertEqual(os.listdir(self.upload_dir), [])

    # ── ownership ──
    def test_other_users_generation_behaves_as_missing(self):
        gen_id = self.new_generation()
        iteration_id = self.iterate(gen_id).get_json()["iteration_id"]
        bob = self.other_user_client()
        self.assertEqual(bob.get(f"/api/generation/{gen_id}").status_code, 404)
        self.assertEqual(bob.patch(f"/api/generation/{gen_id}/favorite", json={}).status_code, 404)
        self.assertEqual(bob.delete(f"/api/generation/{gen_id}", json={}).status_code, 404)
        self.assertEqual(self.sculpt(gen_id, iteration_id, client=bob).status_code, 404)
        self.assertEqual(self.iterate(gen_id, client=bob).status_code, 404)
        self.assertEqual(bob.get("/api/generations").get_json(), [])

    def test_files_are_only_served_to_their_owner(self):
        gen_id = self.sculpted_generation()
        url = self.client.get(f"/api/generation/{gen_id}").get_json()["stl_url"]
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.other_user_client().get(url).status_code, 404)

    # ── sidebar, favourites, delete, suggestions ──
    def test_list_is_newest_first_and_hides_empty_generations(self):
        old, new = self.new_generation(), self.new_generation()
        self.new_generation()  # never used, so not listed
        self.iterate(old, "old")
        self.iterate(new, "new")
        names = [g["name"] for g in self.client.get("/api/generations").get_json()]
        self.assertEqual(names, ["new", "old"])

    def test_favorite_toggles(self):
        gen_id = self.new_generation()
        url = f"/api/generation/{gen_id}/favorite"
        self.assertTrue(self.client.patch(url, json={}).get_json()["is_favorite"])
        self.assertFalse(self.client.patch(url, json={}).get_json()["is_favorite"])

    def test_delete_removes_generation_and_its_files(self):
        gen_id = self.sculpted_generation()
        self.assertEqual(len(os.listdir(self.upload_dir)), 3)  # image, glb, stl
        self.assertEqual(self.client.delete(f"/api/generation/{gen_id}", json={}).status_code, 200)
        self.assertEqual(os.listdir(self.upload_dir), [])
        self.assertEqual(self.client.get(f"/api/generation/{gen_id}").status_code, 404)

    def test_recent_prompts_are_last_five_different_first_prompts(self):
        for prompt in ["a", "b", "A", "c", "d", "e", "f"]:
            gen_id = self.new_generation()
            self.iterate(gen_id, prompt)
            self.iterate(gen_id, "refinement, not a first prompt")
        self.assertEqual(self.client.get("/api/prompts/recent").get_json(), ["f", "e", "d", "c", "A"])


if __name__ == "__main__":
    unittest.main()
