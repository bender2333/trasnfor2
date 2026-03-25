import sys
import socket

from flask import Flask, render_template
from flask_socketio import SocketIO

from config import HOST, PORT, HOSTNAME
from discovery import start_discovery, set_socketio as discovery_set_socketio, get_local_ip
from routes.api import api_bp
from routes.receive import receive_bp, set_socketio as receive_set_socketio
from transfer import set_socketio as transfer_set_socketio

app = Flask(__name__)
app.config['SECRET_KEY'] = 'lan-file-transfer-secret'

socketio = SocketIO(app, cors_allowed_origins='*')

# Inject socketio into modules
discovery_set_socketio(socketio)
receive_set_socketio(socketio)
transfer_set_socketio(socketio)

# Register blueprints
app.register_blueprint(api_bp)
app.register_blueprint(receive_bp)


@app.route('/')
def index():
    """Serve the main page."""
    return render_template('index.html')


@socketio.on('connect')
def handle_connect():
    """Handle new WebSocket connection."""
    from discovery import get_devices
    socketio.emit('device_update', get_devices())


def check_port_available(host, port):
    """Check if the port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def main():
    """Start the application."""
    port = PORT

    # Check if port is available
    if not check_port_available(HOST, port):
        print(f"[Error] Port {port} is already in use.")
        # Try to find an available port
        for p in range(port + 1, port + 100):
            if check_port_available(HOST, p):
                port = p
                print(f"[Info] Using alternative port: {port}")
                break
        else:
            print("[Error] No available port found. Exiting.")
            sys.exit(1)

    local_ip = get_local_ip()

    print("=" * 50)
    print(f"  LAN File Transfer Tool")
    print(f"  Host: {HOSTNAME}")
    print(f"  Local IP: {local_ip}")
    print(f"  Web UI: http://localhost:{port}")
    print(f"  LAN access: http://{local_ip}:{port}")
    print("=" * 50)

    # Start device discovery
    start_discovery()

    # Start Flask with SocketIO
    socketio.run(app, host=HOST, port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()
