import io
import math
import os
import uuid
import threading
import time

import requests

from config import CHUNK_SIZE, PORT

# Reference to socketio (set by app.py)
_socketio = None


def set_socketio(sio):
    """Set the SocketIO instance for emitting progress events."""
    global _socketio
    _socketio = sio


def _calculate_file_chunks(file_size):
    """Calculate the number of chunks needed for a file."""
    if file_size == 0:
        return 1
    return math.ceil(file_size / CHUNK_SIZE)


def _collect_files(path):
    """Collect all files under a path (file or directory).

    Returns list of dicts: [{"absolute_path": ..., "relative_path": ..., "size": ...}]
    """
    path = os.path.abspath(path)
    files = []

    if os.path.isfile(path):
        files.append({
            'absolute_path': path,
            'relative_path': os.path.basename(path),
            'size': os.path.getsize(path),
        })
    elif os.path.isdir(path):
        base_name = os.path.basename(path)
        for root, dirs, filenames in os.walk(path):
            # Include empty directories as zero-size entries
            for d in dirs:
                dir_abs = os.path.join(root, d)
                if not os.listdir(dir_abs):
                    rel = os.path.relpath(dir_abs, os.path.dirname(path))
                    files.append({
                        'absolute_path': dir_abs,
                        'relative_path': rel + '/',
                        'size': 0,
                    })
            for fname in filenames:
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, os.path.dirname(path))
                files.append({
                    'absolute_path': abs_path,
                    'relative_path': rel_path,
                    'size': os.path.getsize(abs_path),
                })

    return files


def _query_resume_status(target_url, task_id):
    """Query target for already-received chunks (for resume)."""
    try:
        resp = requests.get(f"{target_url}/api/receive/status/{task_id}", timeout=5)
        if resp.status_code == 200:
            return resp.json().get('received_chunks', {})
    except Exception:
        pass
    return {}


def send_files(target_ip, paths, task_id=None, target_port=None):
    """Send one or more files/folders to the target device.

    Args:
        target_ip: IP address of the target device
        paths: List of file/folder paths to send
        task_id: Optional task ID (generated if not provided)
        target_port: Target port (defaults to config.PORT)

    Returns:
        dict with transfer result
    """
    if target_port is None:
        target_port = PORT
    if task_id is None:
        task_id = str(uuid.uuid4())

    target_url = f"http://{target_ip}:{target_port}"

    # Collect all files
    all_files = []
    for path in paths:
        all_files.extend(_collect_files(path))

    if not all_files:
        return {'success': False, 'error': 'No files to send'}

    total_size = sum(f['size'] for f in all_files)

    # Phase 1: Handshake
    file_manifest = [
        {'relative_path': f['relative_path'], 'size': f['size']}
        for f in all_files
    ]

    try:
        resp = requests.post(f"{target_url}/api/receive/start", json={
            'task_id': task_id,
            'files': file_manifest,
            'total_size': total_size,
        }, timeout=10)

        if resp.status_code != 200 or not resp.json().get('accepted'):
            error_msg = resp.json().get('error', 'Transfer rejected')
            if _socketio:
                _socketio.emit('transfer_error', {
                    'task_id': task_id,
                    'error_message': error_msg,
                })
            return {'success': False, 'error': error_msg}
    except requests.ConnectionError:
        error_msg = f'Cannot connect to {target_ip}:{target_port}'
        if _socketio:
            _socketio.emit('transfer_error', {
                'task_id': task_id,
                'error_message': error_msg,
            })
        return {'success': False, 'error': error_msg}

    # Query resume status
    received_chunks = _query_resume_status(target_url, task_id)

    # Phase 2: Send chunks
    bytes_sent = 0
    start_time = time.time()

    for file_info in all_files:
        rel_path = file_info['relative_path']
        abs_path = file_info['absolute_path']
        file_size = file_info['size']

        # Skip directory markers
        if rel_path.endswith('/'):
            continue

        # Skip empty files (still handled by complete)
        if file_size == 0:
            continue

        total_chunks = _calculate_file_chunks(file_size)
        already_received = set(received_chunks.get(rel_path, []))

        try:
            with open(abs_path, 'rb') as f:
                for chunk_index in range(total_chunks):
                    # Skip already received chunks (resume)
                    if chunk_index in already_received:
                        f.seek(min((chunk_index + 1) * CHUNK_SIZE, file_size))
                        bytes_sent += min(CHUNK_SIZE, file_size - chunk_index * CHUNK_SIZE)
                        continue

                    chunk_data = f.read(CHUNK_SIZE)
                    if not chunk_data:
                        break

                    # Send chunk
                    files_payload = {
                        'data': (f'chunk_{chunk_index}', io.BytesIO(chunk_data), 'application/octet-stream')
                    }
                    form_data = {
                        'task_id': task_id,
                        'relative_path': rel_path,
                        'chunk_index': str(chunk_index),
                        'total_chunks': str(total_chunks),
                    }

                    resp = requests.post(
                        f"{target_url}/api/receive/chunk",
                        data=form_data,
                        files=files_payload,
                        timeout=60,
                    )

                    if resp.status_code != 200:
                        error_msg = f'Failed to send chunk {chunk_index} of {rel_path}'
                        if _socketio:
                            _socketio.emit('transfer_error', {
                                'task_id': task_id,
                                'error_message': error_msg,
                            })
                        return {'success': False, 'error': error_msg}

                    bytes_sent += len(chunk_data)
                    elapsed = time.time() - start_time
                    speed = bytes_sent / elapsed if elapsed > 0 else 0

                    # Emit progress from sender side
                    if _socketio:
                        progress = round(bytes_sent / total_size * 100, 1) if total_size > 0 else 100
                        _socketio.emit('transfer_progress', {
                            'task_id': task_id,
                            'file': rel_path,
                            'chunk_index': chunk_index,
                            'total_chunks': total_chunks,
                            'progress_percent': progress,
                            'speed': speed,
                            'bytes_sent': bytes_sent,
                            'total_size': total_size,
                        })

        except Exception as e:
            error_msg = f'Error sending {rel_path}: {str(e)}'
            if _socketio:
                _socketio.emit('transfer_error', {
                    'task_id': task_id,
                    'error_message': error_msg,
                })
            return {'success': False, 'error': error_msg}

    # Phase 3: Complete
    try:
        resp = requests.post(f"{target_url}/api/receive/complete", json={
            'task_id': task_id,
        }, timeout=30)

        if resp.status_code == 200 and resp.json().get('completed'):
            if _socketio:
                _socketio.emit('transfer_complete', {
                    'task_id': task_id,
                    'files_count': len(all_files),
                    'total_size': total_size,
                    'direction': 'send',
                })
            return {'success': True, 'files_count': len(all_files), 'total_size': total_size}
        else:
            error_msg = resp.json().get('error', 'Completion failed')
            return {'success': False, 'error': error_msg}
    except Exception as e:
        return {'success': False, 'error': f'Completion error: {str(e)}'}


def send_files_async(target_ip, paths, task_id=None, target_port=None):
    """Send files in a background thread. Returns the task_id immediately."""
    if task_id is None:
        task_id = str(uuid.uuid4())

    thread = threading.Thread(
        target=send_files,
        args=(target_ip, paths, task_id, target_port),
        daemon=True,
        name=f'transfer-{task_id[:8]}',
    )
    thread.start()

    return task_id
