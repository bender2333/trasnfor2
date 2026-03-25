import os
import shutil
import threading

from flask import Blueprint, request, jsonify

from config import SAVE_DIR, CHUNK_SIZE

receive_bp = Blueprint('receive', __name__)

# Track ongoing transfer tasks
# {task_id: {files: [...], total_size: int, received_chunks: {path: set()}, temp_dir: str}}
_tasks = {}
_tasks_lock = threading.Lock()

# Reference to socketio (set by app.py)
_socketio = None


def set_socketio(sio):
    """Set the SocketIO instance for emitting progress events."""
    global _socketio
    _socketio = sio


def _get_safe_path(base_dir, relative_path):
    """Prevent path traversal attacks."""
    # Normalize and ensure the path stays within base_dir
    full_path = os.path.normpath(os.path.join(base_dir, relative_path))
    if not full_path.startswith(os.path.normpath(base_dir)):
        raise ValueError("Path traversal detected")
    return full_path


def _resolve_conflict(file_path):
    """Auto-rename file if it already exists: file.txt -> file(1).txt."""
    if not os.path.exists(file_path):
        return file_path

    base, ext = os.path.splitext(file_path)
    counter = 1
    while os.path.exists(f"{base}({counter}){ext}"):
        counter += 1
    return f"{base}({counter}){ext}"


@receive_bp.route('/api/receive/start', methods=['POST'])
def start_transfer():
    """Phase 1: Handshake - accept or reject an incoming transfer."""
    data = request.get_json()

    task_id = data.get('task_id')
    files = data.get('files', [])
    total_size = data.get('total_size', 0)

    if not task_id or not files:
        return jsonify({'accepted': False, 'error': 'Missing task_id or files'}), 400

    # Check available disk space
    disk_usage = shutil.disk_usage(SAVE_DIR)
    if disk_usage.free < total_size:
        return jsonify({
            'accepted': False,
            'error': f'Insufficient disk space. Need {total_size} bytes, have {disk_usage.free} bytes'
        }), 507

    # Create temp directory for chunks
    temp_dir = os.path.join(SAVE_DIR, f'.tmp_{task_id}')
    os.makedirs(temp_dir, exist_ok=True)

    # Create subdirectories for files
    for file_info in files:
        rel_path = file_info['relative_path']
        file_dir = os.path.dirname(rel_path)
        if file_dir:
            dir_path = _get_safe_path(temp_dir, file_dir)
            os.makedirs(dir_path, exist_ok=True)

    with _tasks_lock:
        _tasks[task_id] = {
            'files': files,
            'total_size': total_size,
            'received_chunks': {},
            'temp_dir': temp_dir,
            'bytes_received': 0,
        }

    # Notify frontend about incoming transfer
    if _socketio:
        sender_ip = request.remote_addr
        _socketio.emit('transfer_incoming', {
            'task_id': task_id,
            'files_count': len(files),
            'total_size': total_size,
            'from_ip': sender_ip,
        })

    return jsonify({'accepted': True, 'task_id': task_id})


@receive_bp.route('/api/receive/chunk', methods=['POST'])
def receive_chunk():
    """Phase 2: Receive a single chunk of a file."""
    task_id = request.form.get('task_id')
    relative_path = request.form.get('relative_path')
    chunk_index = int(request.form.get('chunk_index', 0))
    total_chunks = int(request.form.get('total_chunks', 1))

    if not task_id or not relative_path:
        return jsonify({'received': False, 'error': 'Missing required fields'}), 400

    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return jsonify({'received': False, 'error': 'Unknown task_id'}), 404

    # Get the uploaded chunk data
    chunk_data = request.files.get('data')
    if not chunk_data:
        return jsonify({'received': False, 'error': 'No chunk data'}), 400

    # Write chunk to temp file
    temp_dir = task['temp_dir']
    chunk_filename = f"{relative_path}.chunk_{chunk_index}"
    chunk_path = _get_safe_path(temp_dir, chunk_filename)

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(chunk_path), exist_ok=True)

    chunk_bytes = chunk_data.read()
    with open(chunk_path, 'wb') as f:
        f.write(chunk_bytes)

    # Track received chunk
    with _tasks_lock:
        if relative_path not in task['received_chunks']:
            task['received_chunks'][relative_path] = set()
        task['received_chunks'][relative_path].add(chunk_index)
        task['bytes_received'] += len(chunk_bytes)

        # Calculate progress
        progress_percent = 0
        if task['total_size'] > 0:
            progress_percent = round(task['bytes_received'] / task['total_size'] * 100, 1)

    # Emit progress
    if _socketio:
        _socketio.emit('transfer_progress', {
            'task_id': task_id,
            'file': relative_path,
            'chunk_index': chunk_index,
            'total_chunks': total_chunks,
            'progress_percent': progress_percent,
        })

    return jsonify({'received': True, 'chunk_index': chunk_index})


@receive_bp.route('/api/receive/complete', methods=['POST'])
def complete_transfer():
    """Phase 3: Merge all chunks and finalize the transfer."""
    data = request.get_json()
    task_id = data.get('task_id')

    if not task_id:
        return jsonify({'completed': False, 'error': 'Missing task_id'}), 400

    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return jsonify({'completed': False, 'error': 'Unknown task_id'}), 404

    temp_dir = task['temp_dir']
    files_merged = 0

    try:
        for file_info in task['files']:
            relative_path = file_info['relative_path']
            file_size = file_info['size']

            if file_size == 0:
                # Empty file or directory marker
                final_path = _get_safe_path(SAVE_DIR, relative_path)
                final_path = _resolve_conflict(final_path)
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                with open(final_path, 'wb'):
                    pass
                files_merged += 1
                continue

            total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

            # Merge chunks into final file
            final_path = _get_safe_path(SAVE_DIR, relative_path)
            final_path = _resolve_conflict(final_path)
            os.makedirs(os.path.dirname(final_path), exist_ok=True)

            with open(final_path, 'wb') as out_file:
                for i in range(total_chunks):
                    chunk_filename = f"{relative_path}.chunk_{i}"
                    chunk_path = _get_safe_path(temp_dir, chunk_filename)

                    if not os.path.exists(chunk_path):
                        return jsonify({
                            'completed': False,
                            'error': f'Missing chunk {i} for {relative_path}'
                        }), 400

                    with open(chunk_path, 'rb') as chunk_file:
                        out_file.write(chunk_file.read())

            files_merged += 1

        # Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)

        # Remove task
        with _tasks_lock:
            _tasks.pop(task_id, None)

        # Notify frontend
        if _socketio:
            _socketio.emit('transfer_complete', {
                'task_id': task_id,
                'files_count': files_merged,
                'total_size': task['total_size'],
            })

        return jsonify({'completed': True, 'files_count': files_merged})

    except Exception as e:
        if _socketio:
            _socketio.emit('transfer_error', {
                'task_id': task_id,
                'error_message': str(e),
            })
        return jsonify({'completed': False, 'error': str(e)}), 500


@receive_bp.route('/api/receive/status/<task_id>', methods=['GET'])
def transfer_status(task_id):
    """Query received chunks for resume support."""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return jsonify({
                'task_id': task_id,
                'received_chunks': {}
            })

        # Convert sets to sorted lists for JSON
        received = {
            path: sorted(list(chunks))
            for path, chunks in task['received_chunks'].items()
        }

    return jsonify({
        'task_id': task_id,
        'received_chunks': received,
    })
