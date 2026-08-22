"""SocketIO event handlers for the /pipeline-world namespace. Right now
this is push-only (server -> browser stage updates from pipeline.py) --
no client-originated events are needed yet, but the namespace needs at
least one registered handler for Flask-SocketIO to accept connections to
it cleanly."""
from app.extensions import socketio


@socketio.on("connect", namespace="/pipeline-world")
def handle_connect():
    return True
