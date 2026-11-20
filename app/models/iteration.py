from app.db import db


class Iteration(db.Model):
    __tablename__ = "iterations"
    id = db.Column(db.Integer, primary_key=True)
    generation_id = db.Column(db.Integer, db.ForeignKey("generations.id"), nullable=False, index=True)
    prompt = db.Column(db.String(500), nullable=False)
    image_url = db.Column(db.String(255), nullable=False)  # file name inside UPLOAD_DIR
    task_id = db.Column(db.String(255))  # Meshy task id, kept for reference
    iteration_order = db.Column(db.Integer, nullable=False)
