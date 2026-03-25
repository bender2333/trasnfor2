# LAN File Transfer Tool - Design Spec

**Date**: 2026-03-25
**Status**: Approved

## Overview

A Windows LAN file transfer tool that allows two computers on the same local network to discover each other and transfer files/folders bidirectionally through a Web interface.

## Requirements

- **Platform**: Windows
- **Language**: Python (Flask)
- **Interface**: Web UI (browser-based)
- **Transfer Mode**: Bidirectional (both peers run the program)
- **Discovery**: Automatic LAN discovery via UDP broadcast
- **File Support**: Large files (chunked transfer, resume), folders, no size limit

## Architecture

```
Computer A                                Computer B
+---------------------+                  +---------------------+
|  Flask Server :8765  |                  |  Flask Server :8765  |
|  +---------------+  |   UDP broadcast  |  +---------------+  |
|  | Discovery     |<-+------------------+->| Discovery     |  |
|  +---------------+  |   port 5354      |  +---------------+  |
|  +---------------+  |                  |  +---------------+  |
|  | File Transfer |<-+--HTTP chunked--->+->| File Transfer |  |
|  +---------------+  |                  |  +---------------+  |
|  +---------------+  |                  |  +---------------+  |
|  | Web Frontend  |  |                  |  | Web Frontend  |  |
|  +---------------+  |                  |  +---------------+  |
+---------------------+                  +---------------------+
     ^                                        ^
     | browser localhost:8765                  | browser localhost:8765
     +-- User A                               +-- User B
```

### Core Flow

1. Both computers run `python app.py`, starting Flask on `0.0.0.0:8765`
2. UDP broadcast module announces presence every 3 seconds, listens for others
3. User opens browser at `localhost:8765`, sees discovered devices
4. User selects target device, picks files/folders, initiates transfer
5. Files are chunked and sent via HTTP POST to the target's Flask server
6. WebSocket pushes real-time transfer progress to the frontend

## Project Structure

```
file_transfor/
├── app.py                  # Entry point: starts Flask server and UDP discovery
├── config.py               # Configuration constants
├── discovery.py            # UDP broadcast device discovery
├── transfer.py             # File chunking and sending logic (sender side)
├── routes/
│   ├── __init__.py
│   ├── api.py              # REST API routes (device list, initiate transfer)
│   └── receive.py          # Receiver routes (accept chunks, merge files)
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── main.js
├── templates/
│   └── index.html
├── received_files/         # Default save directory
└── requirements.txt        # flask, flask-socketio, requests
```

### Module Responsibilities

| Module | Responsibility | Interface |
|--------|---------------|-----------|
| `config.py` | Centralized constants | `PORT`, `UDP_PORT`, `CHUNK_SIZE`, `SAVE_DIR` |
| `discovery.py` | UDP broadcast/listen, maintain online device list | `start_discovery()`, `get_devices()` |
| `transfer.py` | Chunk files, POST chunks to target | `send_file(target_ip, file_path)`, `send_folder(target_ip, folder_path)` |
| `routes/api.py` | Frontend-facing API | `GET /api/devices`, `POST /api/send` |
| `routes/receive.py` | Receive chunks, write to disk, merge | `POST /api/receive/start`, `POST /api/receive/chunk`, `POST /api/receive/complete` |

### Dependencies (3 only)

- `flask` - Web framework
- `flask-socketio` - WebSocket support
- `requests` - HTTP client for sending chunks to peer

## Protocols

### Device Discovery (UDP Broadcast)

```json
// Broadcast packet (every 3 seconds, port 5354):
{
  "action": "announce",
  "hostname": "DESKTOP-ABC",
  "ip": "192.168.1.100",
  "port": 8765,
  "timestamp": 1711360000
}
```

- Device timeout: marked offline after 10 seconds without broadcast
- Broadcast address: `255.255.255.255` on port 5354

### File Transfer Protocol

#### Phase 1 - Handshake

```
POST http://<target>:8765/api/receive/start
{
  "task_id": "uuid-xxx",
  "files": [
    {"relative_path": "docs/readme.md", "size": 2048},
    {"relative_path": "photo.jpg", "size": 5242880}
  ],
  "total_size": 5244928
}
Response: {"accepted": true, "task_id": "uuid-xxx"}
```

- Receiver checks available disk space before accepting
- Rejects if insufficient space

#### Phase 2 - Chunked Transfer

```
POST http://<target>:8765/api/receive/chunk
Content-Type: multipart/form-data
Fields:
  task_id: "uuid-xxx"
  relative_path: "photo.jpg"
  chunk_index: 0
  total_chunks: 5
  data: <binary>
Response: {"received": true, "chunk_index": 0}
```

- Chunk size: 1MB (configurable)
- Sequential per file, parallel files possible in future

#### Phase 3 - Completion

```
POST http://<target>:8765/api/receive/complete
{
  "task_id": "uuid-xxx"
}
Response: {"completed": true, "files_count": 2}
```

### Resume Support

```
GET http://<target>:8765/api/receive/status/<task_id>
Response:
{
  "task_id": "uuid-xxx",
  "received_chunks": {
    "photo.jpg": [0, 1, 2],
    "docs/readme.md": [0]
  }
}
```

Sender skips confirmed chunks, only sends incomplete ones.

### WebSocket Events

| Event | Direction | Payload |
|-------|-----------|---------|
| `device_update` | Server -> Client | Device list (online/offline changes) |
| `transfer_progress` | Server -> Client | `{task_id, file, progress_percent, speed}` |
| `transfer_complete` | Server -> Client | `{task_id, files_count, total_size}` |
| `transfer_error` | Server -> Client | `{task_id, error_message}` |

## Frontend Design

Single-page application with 3 zones:

```
+------------------------------------------+
|  LAN File Transfer                       |
+--------------+---------------------------+
|              |                           |
|  Online      |   Transfer Area           |
|  Devices     |                           |
|              |   [ Select Files ] [ Select Folder ] |
|  * DESKTOP-A |                           |
|  * LAPTOP-B  |   Selected: 3 files (12.5MB) |
|              |   Target: DESKTOP-A       |
|              |                           |
|              |   [ Start Transfer ]      |
|              |                           |
|              +---------------------------+
|              |   Transfer Log            |
|              |                           |
|              |   done  photo.jpg   100%  |
|              |   up    video.mp4  [=== ] 67% 5.2MB/s |
|              |   wait  doc.pdf    waiting |
|              |                           |
+--------------+---------------------------+
```

### Key Interactions

- Left panel: real-time device list (WebSocket-driven)
- Click device to select as transfer target
- Drag-and-drop files/folders onto the page
- Real-time progress bars with transfer speed
- Notification on transfer complete/error

## Error Handling

| Scenario | Handling |
|----------|----------|
| Target device offline | Check device status before transfer; alert user if offline |
| Network disconnect mid-transfer | Preserve received chunks; resume on reconnect |
| Filename conflict | Auto-rename: `file.txt` -> `file(1).txt` |
| Insufficient disk space | Check at handshake phase; reject with clear message |
| Large files (>1GB) | Handled naturally by chunking; no upper limit |
| Empty folders | Record directory structure; create empty dirs on receiver |
| Special characters in filenames | URL-encode during transfer; preserve original name on save |
| Port already in use | Detect at startup; prompt user or auto-select available port |
