from flask import current_app

from app.services import meshy_service, mock_service


def ai():
    """The AI backend to use: real Meshy, or the offline mock when USE_MOCK_AI=1."""
    return mock_service if current_app.config["USE_MOCK_AI"] else meshy_service
