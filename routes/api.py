import os
import uuid

from flask import Blueprint, request, jsonify

from discovery import get_devices, get_local_ip
from transfer import send_files_async
from config import HOSTNAME, PORT

api_bp = Blueprint('api', __name__)


@api_bp.route('/api/devices', methods=['GET'])
def list_devices():
    """Return list of discovered devices on the LAN."""
    devices = get_devices()
    local_ip = get_local_ip()
    return jsonify({
        'devices': devices,
        'self': {
            'hostname': HOSTNAME,
            'ip': local_ip,
            'port': PORT,
        }
    })


@api_bp.route('/api/send', methods=['POST'])
def initiate_send():
    """Initiate a file transfer to a target device.

    Expects JSON:
    {
        "target_ip": "192.168.1.100",
        "target_port": 8765,
        "paths": ["/path/to/file1", "/path/to/folder1"]
    }
    """
    data = request.get_json()

    target_ip = data.get('target_ip')
    target_port = data.get('target_port', PORT)
    paths = data.get('paths', [])

    if not target_ip:
        return jsonify({'success': False, 'error': 'Missing target_ip'}), 400

    if not paths:
        return jsonify({'success': False, 'error': 'No files selected'}), 400

    # Validate paths exist
    valid_paths = []
    for p in paths:
        if os.path.exists(p):
            valid_paths.append(p)
        else:
            return jsonify({'success': False, 'error': f'Path not found: {p}'}), 400

    task_id = str(uuid.uuid4())

    # Start async transfer
    send_files_async(target_ip, valid_paths, task_id=task_id, target_port=target_port)

    return jsonify({
        'success': True,
        'task_id': task_id,
        'message': 'Transfer started',
    })


@api_bp.route('/api/browse', methods=['GET'])
def browse_local():
    """Browse local filesystem for file selection.

    Query params:
        path: directory path to browse (default: user home)
    """
    browse_path = request.args.get('path', os.path.expanduser('~'))

    if not os.path.exists(browse_path):
        return jsonify({'success': False, 'error': 'Path not found'}), 404

    if not os.path.isdir(browse_path):
        return jsonify({'success': False, 'error': 'Not a directory'}), 400

    entries = []
    try:
        for entry in os.scandir(browse_path):
            try:
                entries.append({
                    'name': entry.name,
                    'path': entry.path.replace('\\', '/'),
                    'is_dir': entry.is_dir(),
                    'size': entry.stat().st_size if entry.is_file() else 0,
                })
            except PermissionError:
                continue
    except PermissionError:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    # Sort: directories first, then files, alphabetically
    entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))

    # Get parent directory
    parent = os.path.dirname(browse_path).replace('\\', '/')

    return jsonify({
        'success': True,
        'current_path': browse_path.replace('\\', '/'),
        'parent': parent,
        'entries': entries,
    })
