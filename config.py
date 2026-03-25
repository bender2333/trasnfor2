import os
import socket

# Server configuration
PORT = 8765
HOST = '0.0.0.0'

# UDP discovery configuration
UDP_PORT = 5354
UDP_BROADCAST_ADDR = '255.255.255.255'
BROADCAST_INTERVAL = 2  # seconds between broadcasts (reduced for reliability)
DEVICE_TIMEOUT = 15  # seconds without broadcast -> offline (allows ~7 missed broadcasts)

# File transfer configuration
CHUNK_SIZE = 1 * 1024 * 1024  # 1MB per chunk

# Save directory for received files
SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'received_files')

# Ensure save directory exists
os.makedirs(SAVE_DIR, exist_ok=True)

# Get hostname
HOSTNAME = socket.gethostname()
