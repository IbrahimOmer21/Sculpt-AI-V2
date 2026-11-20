"""Meshy API calls: text-to-image, image-to-image and image-to-3D.

Every function saves its output into UPLOAD_DIR and returns bare file names (never full paths).
"""
import base64
import mimetypes
import os
import time

import requests
from flask import current_app

MESHY_API_BASE = "https://api.meshy.ai/openapi/v1"
API_TIMEOUT = 30        # seconds for one API request
DOWNLOAD_TIMEOUT = 120  # seconds for one file download
IMAGE_TASK_TIMEOUT = 60
SCULPTURE_TASK_TIMEOUT = 300


class MeshyError(Exception):
    """Anything that went wrong talking to Meshy. Routes turn it into a JSON 502."""


def _headers():
    return {"Authorization": f"Bearer {current_app.config['MESHY_API_KEY']}"}


def _request(method, url, **kwargs):
    try:
        response = requests.request(method, url, headers=_headers(), timeout=API_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise MeshyError(f"Could not reach Meshy: {e}") from e
    if not response.ok:
        raise MeshyError(f"Meshy {method} {url} failed: {response.status_code} {response.text[:300]}")
    try:
        return response.json()
    except ValueError as e:  # e.g. an HTML error page from a proxy
        raise MeshyError(f"Meshy {method} {url} returned non-JSON: {response.text[:300]}") from e


def image_to_base64(image_path):
    """Local files aren't on the public web, so Meshy gets them as a base64 data URI."""
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    mime = mimetypes.guess_type(image_path)[0] or "image/png"  # uploads can be JPG
    return f"data:{mime};base64,{encoded}"


POLL_RETRIES = 3  # failed status checks in a row before giving up


def poll_task(endpoint, task_id, interval=5, timeout=SCULPTURE_TASK_TIMEOUT):
    """Ask Meshy for the task status every `interval` seconds until it succeeds, fails or times out."""
    deadline = time.monotonic() + timeout
    failures = 0
    while time.monotonic() < deadline:
        try:
            task = _request("GET", f"{MESHY_API_BASE}/{endpoint}/{task_id}")
        except MeshyError:
            # The task keeps running (and is already paid for), so one network blip shouldn't throw it away.
            failures += 1
            if failures >= POLL_RETRIES:
                raise
            time.sleep(interval)
            continue
        failures = 0
        status = task.get("status")
        if status == "SUCCEEDED":
            return task
        if status in ("FAILED", "CANCELED"):
            message = (task.get("task_error") or {}).get("message", "Unknown error")
            raise MeshyError(f"Meshy task failed: {message}")
        time.sleep(interval)
    raise MeshyError(f"Meshy task timed out after {timeout} seconds")


def download_file(url, filename):
    """Save a Meshy result URL into UPLOAD_DIR under `filename`."""
    destination = os.path.join(current_app.config["UPLOAD_DIR"], filename)
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
            response.raise_for_status()
            with open(destination, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
    except requests.RequestException as e:
        if os.path.exists(destination):
            os.remove(destination)  # don't leave half-written files behind
        raise MeshyError(f"Download failed: {e}") from e
    return filename


def _image_task(endpoint, body):
    task_id = _request("POST", f"{MESHY_API_BASE}/{endpoint}", json=body)["result"]
    task = poll_task(endpoint, task_id, timeout=IMAGE_TASK_TIMEOUT)
    filename = download_file(task["image_urls"][0], f"{task_id}.png")
    return {"image_url": filename, "task_id": task_id}


def generate_image(prompt):
    """First iteration: text prompt -> image."""
    return _image_task("text-to-image", {"ai_model": "nano-banana", "prompt": prompt, "aspect_ratio": "1:1"})


def refine_image(prompt, previous_image_path):
    """Iterations 2 and 3: previous image + new prompt -> refined image."""
    return _image_task("image-to-image", {
        "ai_model": "nano-banana",
        "prompt": prompt,
        "reference_image_urls": [image_to_base64(previous_image_path)],
    })


def generate_sculpture(image_path):
    """Image (generated or uploaded) -> GLB + STL. The image is re-sent as base64, no task chaining."""
    task_id = _request("POST", f"{MESHY_API_BASE}/image-to-3d", json={"image_url": image_to_base64(image_path)})["result"]
    task = poll_task("image-to-3d", task_id, timeout=SCULPTURE_TASK_TIMEOUT)

    model_urls = task.get("model_urls") or {}
    if not model_urls.get("glb") or not model_urls.get("stl"):
        raise MeshyError("Meshy response is missing the GLB or STL file")
    return {
        "sculpture_url": download_file(model_urls["glb"], f"{task_id}.glb"),
        "stl_url": download_file(model_urls["stl"], f"{task_id}.stl"),
    }
