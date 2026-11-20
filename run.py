import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Debug stays off unless FLASK_DEBUG=1: its in-browser debugger lets anyone who reaches it run code.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
