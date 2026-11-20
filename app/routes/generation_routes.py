import os
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.db import db
from app.models import Generation, Iteration
from app.services import ai

generation_bp = Blueprint("generation", __name__)

# A job "in progress" for longer than this is assumed to have crashed and may be retried.
# Each is comfortably above the worst case in meshy_service (task timeout + API calls + downloads).
STALE_SCULPTURE = timedelta(minutes=15)  # worst case is about 10 minutes
STALE_IMAGE = timedelta(minutes=5)       # worst case is about 3.5 minutes
IMAGE_SIGNATURES = {".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",)}


def utcnow():
    # Naive UTC, to match SQLite's CURRENT_TIMESTAMP used for created_at.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_owned_generation(generation_id):
    """The only way routes load a generation: someone else's generation behaves as if it doesn't exist."""
    generation = Generation.query.filter_by(id=generation_id, user_id=current_user.id).first()
    if generation is None:
        abort(404, "Generation not found.")
    return generation


def claim(generation_id, column, stale):
    """Mark a job as started with one atomic UPDATE, so a double click or a second tab can't start it twice.

    Returns False when the same job is already running (and not stale).
    """
    now = utcnow()
    claimed = (Generation.query
               .filter(Generation.id == generation_id, or_(column.is_(None), column < now - stale))
               .update({column: now}, synchronize_session=False))
    db.session.commit()
    return bool(claimed)


def release(generation_id, column):
    db.session.rollback()
    Generation.query.filter_by(id=generation_id).update({column: None}, synchronize_session=False)
    db.session.commit()


def is_running(started_at, stale):
    return bool(started_at) and started_at > utcnow() - stale


def remove_files(names):
    for name in filter(None, names):
        if os.path.exists(upload_path(name)):
            os.remove(upload_path(name))


def file_url(filename):
    return url_for("generation.serve_file", filename=filename) if filename else None


def upload_path(filename):
    return os.path.join(current_app.config["UPLOAD_DIR"], os.path.basename(filename))


def serialize_iteration(iteration):
    return {
        "iteration_id": iteration.id,
        "prompt": iteration.prompt,
        "image_url": file_url(iteration.image_url),
        "iteration_order": iteration.iteration_order,
    }


def serialize_generation(generation):
    return {
        "generation_id": generation.id,
        "name": generation.name,
        "is_favorite": generation.is_favorite,
        "created_at": generation.created_at.isoformat() if generation.created_at else None,
        "sculpture_url": file_url(generation.sculpture_url),
        "stl_url": file_url(generation.stl_url),
        "source_image_url": file_url(generation.source_image_url),
        "in_progress": bool(generation.sculpted_at and not generation.sculpture_url),
        "iterations": [serialize_iteration(i) for i in generation.iterations],
    }


@generation_bp.route("/")
@login_required
def index():
    return render_template("index.html")


@generation_bp.route("/api/generation", methods=["POST"])
@login_required
def new_generation():
    generation = Generation(user_id=current_user.id)
    db.session.add(generation)
    db.session.commit()
    return {"generation_id": generation.id}, 201


@generation_bp.route("/api/generations")
@login_required
def list_generations():
    generations = (current_user.generations
                   .filter(or_(Generation.name.isnot(None), Generation.source_image_url.isnot(None)))
                   .order_by(Generation.created_at.desc(), Generation.id.desc()).all())
    return [{
        "generation_id": g.id,
        "name": g.name,
        "is_favorite": g.is_favorite,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "thumbnail_url": file_url(g.source_image_url),
        "has_sculpture": bool(g.sculpture_url),
    } for g in generations]


@generation_bp.route("/api/prompts/recent")
@login_required
def recent_prompts():
    rows = (db.session.query(Iteration.prompt).join(Generation)
            .filter(Generation.user_id == current_user.id, Iteration.iteration_order == 1)
            .order_by(Generation.created_at.desc(), Generation.id.desc()).limit(50))
    prompts, seen = [], set()
    for (prompt,) in rows:
        key = prompt.strip().lower()
        if key not in seen:
            seen.add(key)
            prompts.append(prompt)
        if len(prompts) == 5:
            break
    return prompts


@generation_bp.route("/api/generation/<int:generation_id>")
@login_required
def get_generation(generation_id):
    return serialize_generation(get_owned_generation(generation_id))


@generation_bp.route("/api/generation/<int:generation_id>/favorite", methods=["PATCH"])
@login_required
def toggle_favorite(generation_id):
    generation = get_owned_generation(generation_id)
    generation.is_favorite = not generation.is_favorite
    db.session.commit()
    return {"is_favorite": generation.is_favorite}


@generation_bp.route("/api/generation/<int:generation_id>", methods=["DELETE"])
@login_required
def delete_generation(generation_id):
    generation = get_owned_generation(generation_id)
    # Deleting mid-job would leave the job's downloaded files with nothing pointing at them.
    if ((not generation.sculpture_url and is_running(generation.sculpted_at, STALE_SCULPTURE))
            or is_running(generation.image_started_at, STALE_IMAGE)):
        abort(409, "This generation is still being made. Delete it once it has finished.")
    files = {generation.sculpture_url, generation.stl_url, generation.source_image_url}
    files.update(i.image_url for i in generation.iterations)
    db.session.delete(generation)  # iterations go too (cascade)
    db.session.commit()
    remove_files(files)  # only after the commit succeeded, so a failed delete never loses them
    return {"message": "Generation deleted."}


@generation_bp.route("/api/generation/<int:generation_id>/sculpture", methods=["POST"])
@login_required
def create_sculpture(generation_id):
    generation = get_owned_generation(generation_id)
    iteration_id = (request.get_json(silent=True) or {}).get("iteration_id")
    if not isinstance(iteration_id, int):
        abort(400, "Choose an image to sculpt.")
    iteration = Iteration.query.filter_by(id=iteration_id, generation_id=generation.id).first()
    if iteration is None:
        abort(404, "Image not found.")
    return run_sculpture(generation, iteration.image_url)


@generation_bp.route("/api/generation/<int:generation_id>/upload", methods=["POST"])
@login_required
def upload_image(generation_id):
    """The user's own PNG/JPG skips image generation and goes straight to 3D."""
    generation = get_owned_generation(generation_id)
    if generation.sculpted_at or generation.iterations:
        abort(409, "Images can only be uploaded to a new, empty generation.")

    file = request.files.get("image")
    if file is None or not file.filename:
        abort(400, "No image was sent.")
    original_name, extension = os.path.splitext(os.path.basename(file.filename))
    extension = extension.lower()
    if extension not in IMAGE_SIGNATURES or not file.stream.read(8).startswith(IMAGE_SIGNATURES[extension]):
        abort(400, "Only PNG and JPG images are allowed.")
    file.stream.seek(0)

    # Never save under the user's file name: "../../app/config.py" would overwrite our code.
    filename = f"{uuid.uuid4()}{extension}"
    file.save(upload_path(filename))
    try:
        return run_sculpture(generation, filename, name=original_name[:50] or "Uploaded image")
    except Exception:
        remove_files([filename])  # sculpting failed or was refused, nothing refers to the file
        raise


def run_sculpture(generation, image_filename, name=None):
    """Shared by sculpture and upload: check limits, mark in progress, call the AI, save the result."""
    if generation.sculpture_url:
        abort(403, "This generation already has a sculpture.")
    if is_running(generation.image_started_at, STALE_IMAGE):
        abort(409, "An image is still being made. Sculpt once it has finished.")

    now = utcnow()
    used_today = (Generation.query
                  .filter(Generation.user_id == current_user.id, Generation.sculpted_at >= now - timedelta(hours=24))
                  .count())
    if used_today >= current_app.config["SCULPTURES_PER_DAY"]:
        abort(429, f"You've reached today's limit of {current_app.config['SCULPTURES_PER_DAY']} sculptures. "
                   "Please try again later.")

    # shortcut: two tabs sculpting *different* generations at once can pass the daily check together; fine for a local demo.
    generation_id = generation.id
    if not claim(generation_id, Generation.sculpted_at, STALE_SCULPTURE):
        abort(409, "This sculpture is already being made.")

    try:
        result = ai().generate_sculpture(upload_path(image_filename))
    except Exception:
        release(generation_id, Generation.sculpted_at)  # failures don't count towards the daily limit
        raise

    generation = db.session.get(Generation, generation_id, populate_existing=True)
    if generation is None:  # deleted while we waited (only possible once the job looked stale)
        remove_files(result.values())
        abort(404, "Generation not found.")
    if name:
        generation.name = name
    generation.source_image_url = image_filename
    generation.sculpture_url = result["sculpture_url"]
    generation.stl_url = result["stl_url"]
    db.session.commit()
    return serialize_generation(generation)


@generation_bp.route("/files/<filename>")
@login_required
def serve_file(filename):
    owned = (Generation.query.filter(
                Generation.user_id == current_user.id,
                or_(Generation.sculpture_url == filename, Generation.stl_url == filename,
                    Generation.source_image_url == filename)).first()
             or Iteration.query.join(Generation)
                .filter(Generation.user_id == current_user.id, Iteration.image_url == filename).first())
    if owned is None:
        abort(404, "File not found.")
    return send_from_directory(current_app.config["UPLOAD_DIR"], filename)
