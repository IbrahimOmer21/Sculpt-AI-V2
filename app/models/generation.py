from app.db import db


class Generation(db.Model):
    __tablename__ = "generations"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(255))
    is_favorite = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # File columns hold bare file names inside UPLOAD_DIR, never full server paths.
    sculpture_url = db.Column(db.String(255))     # GLB
    stl_url = db.Column(db.String(255))
    source_image_url = db.Column(db.String(255))  # the image that was sculpted, also the history thumbnail
    # Set when sculpting starts; sculpted_at set + sculpture_url empty means "in progress".
    sculpted_at = db.Column(db.DateTime)
    # Set while an image is being generated, so two requests can't both make the "last" image.
    image_started_at = db.Column(db.DateTime)

    iterations = db.relationship(
        "Iteration", backref="generation", order_by="Iteration.iteration_order",
        cascade="all, delete-orphan",
    )
