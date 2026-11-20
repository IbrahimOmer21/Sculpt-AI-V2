from flask import Blueprint, abort, current_app, request
from flask_login import login_required

from app.db import db
from app.models import Generation, Iteration
from app.routes.generation_routes import (STALE_IMAGE, claim, get_owned_generation, release,
                                          serialize_iteration, upload_path)
from app.services import ai

iteration_bp = Blueprint("iteration", __name__)

MAX_PROMPT_LENGTH = 500


@iteration_bp.route("/api/generation/<int:generation_id>/iterate", methods=["POST"])
@login_required
def new_iteration(generation_id):
    generation = get_owned_generation(generation_id)

    prompt = (request.get_json(silent=True) or {}).get("prompt")
    prompt = prompt.strip() if isinstance(prompt, str) else ""
    # Checked before calling Meshy, so a blank prompt never spends credits.
    if not prompt:
        abort(400, "Please type a prompt first.")
    if len(prompt) > MAX_PROMPT_LENGTH:
        abort(400, f"Prompts can be at most {MAX_PROMPT_LENGTH} characters.")

    if not claim(generation.id, Generation.image_started_at, STALE_IMAGE):
        abort(409, "An image is already being made. Please wait for it to finish.")

    try:
        # The limits are checked after claiming (the commit reloaded the generation), so a request that just
        # finished in another tab is counted.
        if generation.sculpted_at:
            abort(403, "This generation already has a sculpture.")
        count = len(generation.iterations)
        if count >= current_app.config["ITERATIONS_PER_GENERATION"]:
            abort(403, "Image generation limit reached.")

        if count == 0:
            result = ai().generate_image(prompt)
            generation.name = prompt[:50]
        else:
            result = ai().refine_image(prompt, upload_path(generation.iterations[-1].image_url))

        iteration = Iteration(generation_id=generation.id, prompt=prompt, image_url=result["image_url"],
                              task_id=result["task_id"], iteration_order=count + 1)
        db.session.add(iteration)
        generation.image_started_at = None
        db.session.commit()
    except Exception:
        release(generation_id, Generation.image_started_at)
        raise
    return {**serialize_iteration(iteration), "name": generation.name}, 201
