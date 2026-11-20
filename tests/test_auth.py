import unittest
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from tests.helpers import AppTestCase


class AuthTests(AppTestCase):
    def test_signup_logs_the_user_in(self):
        response = self.signup()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_pages_and_api_require_login(self):
        self.assertIn("/login", self.client.get("/").headers["Location"])
        response = self.client.get("/api/generations")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.get_json())

    def test_short_password_is_rejected(self):
        response = self.signup(password="short")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"at least 8 characters", response.data)

    def test_email_without_at_is_rejected(self):
        self.assertEqual(self.signup(email="not-an-email").status_code, 400)

    def test_email_is_case_insensitive_and_unique(self):
        self.signup(email="Ana@Example.com")
        self.client.post("/logout")
        self.assertEqual(self.signup(email="ana@example.COM").status_code, 400)
        self.assertEqual(self.client.post("/login", data={"email": "ANA@example.com", "password": "password123"}).status_code, 302)

    def test_wrong_password_is_rejected(self):
        self.signup()
        self.client.post("/logout")
        response = self.client.post("/login", data={"email": "ana@example.com", "password": "wrong-password"})
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"incorrect", response.data)

    def test_logout_ends_the_session(self):
        self.signup()
        self.client.post("/logout")
        self.assertEqual(self.client.get("/api/generations").status_code, 401)

    def test_remember_me_sets_a_30_day_cookie(self):
        response = self.client.post("/signup", data={"email": "a@b.c", "password": "password123", "remember": "1"})
        cookie = next(c for c in response.headers.getlist("Set-Cookie") if c.startswith("remember_token="))
        self.assertIn("SameSite=Lax", cookie)
        expires = parsedate_to_datetime(cookie.split("Expires=")[1].split(";")[0])
        self.assertAlmostEqual((expires - datetime.now(timezone.utc)).days, 30, delta=1)


if __name__ == "__main__":
    unittest.main()
