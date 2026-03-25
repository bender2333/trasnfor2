import json
import socket
import struct
import threading
import time

from config import UDP_PORT, UDP_BROADCAST_ADDR, BROADCAST_INTERVAL, DEVICE_TIMEOUT, PORT, HOSTNAME

# Thread-safe device registry
_devices = {}
_lock = threading.Lock()

# Reference to socketio instance (set by app.py)
_socketio = None

# Interval (seconds) to refresh the local IP list used for filtering own broadcasts
_LOCAL_IP_REFRESH_INTERVAL = 10


def set_socketio(sio):
    """Set the SocketIO instance for emitting device updates."""
    global _socketio
    _socketio = sio


def _is_private_lan_ip(ip):
    """Check if an IP is a common private LAN address (not VPN/virtual)."""
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    # 192.168.x.x
    if octets[0] == 192 and octets[1] == 168:
        return True
    # 10.x.x.x
    if octets[0] == 10:
        return True
    # 172.16.0.0 - 172.31.255.255
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return True
    return False


def get_local_ip():
    """Get the local LAN IP address of this machine.

    Prioritizes real LAN IPs (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
    over VPN/virtual adapter addresses like 198.18.x.x.
    """
    all_ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip != '127.0.0.1' and ip not in all_ips:
                all_ips.append(ip)
    except Exception:
        pass

    # Prefer private LAN IPs
    lan_ips = [ip for ip in all_ips if _is_private_lan_ip(ip)]
    if lan_ips:
        return lan_ips[0]

    # Fallback: any non-loopback IP
    if all_ips:
        return all_ips[0]

    # Last resort: connect-based detection
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def _get_subnet_broadcast_addresses():
    """Calculate subnet-directed broadcast addresses for all local interfaces.

    For example, 192.168.1.100/24 -> 192.168.1.255
    This is more reliable than 255.255.255.255 on many OS/router configurations.
    """
    broadcast_addrs = set()

    # Try platform-specific methods first
    try:
        import netifaces
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                for addr_info in addrs[netifaces.AF_INET]:
                    if 'broadcast' in addr_info:
                        broadcast_addrs.add(addr_info['broadcast'])
    except ImportError:
        pass

    # Fallback: estimate common subnet broadcasts from known local IPs
    if not broadcast_addrs:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip == '127.0.0.1':
                    continue
                if _is_private_lan_ip(ip):
                    parts = ip.split('.')
                    # Assume /24 subnet (most common for home/office LANs)
                    broadcast_addrs.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
        except Exception:
            pass

        # Also try connect-based IP detection
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            if _is_private_lan_ip(ip):
                parts = ip.split('.')
                broadcast_addrs.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
        except Exception:
            pass

    return broadcast_addrs


def get_devices():
    """Return list of currently online devices."""
    with _lock:
        now = time.time()
        online = []
        for key, device in list(_devices.items()):
            if now - device['last_seen'] < DEVICE_TIMEOUT:
                online.append({
                    'hostname': device['hostname'],
                    'ip': device['ip'],
                    'port': device['port'],
                })
            else:
                del _devices[key]
        return online


def _create_broadcast_socket():
    """Create a new UDP broadcast socket with proper options."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Set a timeout so sendto doesn't block forever on error
    sock.settimeout(2.0)
    return sock


def _broadcast_loop():
    """Periodically broadcast this device's presence via UDP.

    Sends to both 255.255.255.255 (limited broadcast) and subnet-directed
    broadcast addresses for maximum compatibility across different OS and
    network configurations.
    """
    sock = None

    while True:
        try:
            # Recreate socket if needed (handles network interface changes)
            if sock is None:
                sock = _create_broadcast_socket()

            local_ip = get_local_ip()
            payload = json.dumps({
                'action': 'announce',
                'hostname': HOSTNAME,
                'ip': local_ip,
                'port': PORT,
                'timestamp': int(time.time()),
            }).encode('utf-8')

            # Send to limited broadcast address (255.255.255.255)
            try:
                sock.sendto(payload, (UDP_BROADCAST_ADDR, UDP_PORT))
            except Exception:
                # Socket may be broken, recreate on next iteration
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None

            # Also send to subnet-directed broadcast addresses
            # This is more reliable on many systems where 255.255.255.255 is blocked
            subnet_addrs = _get_subnet_broadcast_addresses()
            for addr in subnet_addrs:
                if addr == UDP_BROADCAST_ADDR:
                    continue  # Already sent to this
                try:
                    if sock is None:
                        sock = _create_broadcast_socket()
                    sock.sendto(payload, (addr, UDP_PORT))
                except Exception:
                    try:
                        sock.close()
                    except Exception:
                        pass
                    sock = None

        except Exception as e:
            print(f"[Discovery] Broadcast error: {e}")
            # Ensure socket is recreated after any error
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
                sock = None

        time.sleep(BROADCAST_INTERVAL)


def _get_all_local_ips():
    """Get all local IP addresses (for filtering own broadcasts)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass

    # Also try the connect-based method
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass

    ips.add('127.0.0.1')
    ips.add(get_local_ip())
    return ips


def _listen_loop():
    """Listen for UDP broadcast announcements from other devices.

    Periodically refreshes local IP list to handle network changes
    (DHCP renewal, WiFi reconnect, etc.).
    """
    local_ips = _get_all_local_ips()
    last_ip_refresh = time.time()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(('', UDP_PORT))
    sock.settimeout(1.0)

    while True:
        try:
            # Periodically refresh local IPs to handle network changes
            now = time.time()
            if now - last_ip_refresh >= _LOCAL_IP_REFRESH_INTERVAL:
                local_ips = _get_all_local_ips()
                last_ip_refresh = now

            data, addr = sock.recvfrom(4096)
            message = json.loads(data.decode('utf-8'))

            if message.get('action') != 'announce':
                continue

            device_ip = message.get('ip', addr[0])

            # Skip our own broadcasts (check all local IPs)
            if device_ip in local_ips or addr[0] in local_ips:
                continue

            key = f"{device_ip}:{message.get('port', PORT)}"
            is_new = key not in _devices

            with _lock:
                _devices[key] = {
                    'hostname': message.get('hostname', 'Unknown'),
                    'ip': device_ip,
                    'port': message.get('port', PORT),
                    'last_seen': time.time(),
                }

            # Notify frontend of device changes
            if is_new and _socketio:
                _socketio.emit('device_update', get_devices())

        except socket.timeout:
            continue
        except json.JSONDecodeError:
            # Ignore malformed packets
            continue
        except Exception as e:
            print(f"[Discovery] Listen error: {e}")
            time.sleep(1)


def _cleanup_loop():
    """Periodically remove timed-out devices."""
    while True:
        time.sleep(DEVICE_TIMEOUT)
        with _lock:
            now = time.time()
            removed = False
            for key in list(_devices.keys()):
                if now - _devices[key]['last_seen'] >= DEVICE_TIMEOUT:
                    del _devices[key]
                    removed = True
            if removed and _socketio:
                _socketio.emit('device_update', get_devices())


def start_discovery():
    """Start all discovery threads (broadcast, listen, cleanup)."""
    threads = [
        threading.Thread(target=_broadcast_loop, daemon=True, name='udp-broadcast'),
        threading.Thread(target=_listen_loop, daemon=True, name='udp-listen'),
        threading.Thread(target=_cleanup_loop, daemon=True, name='device-cleanup'),
    ]
    for t in threads:
        t.start()

    local_ip = get_local_ip()
    subnet_addrs = _get_subnet_broadcast_addresses()
    addrs_str = ', '.join(subnet_addrs) if subnet_addrs else 'none detected'
    print(f"[Discovery] Started on {local_ip}, UDP port {UDP_PORT}")
    print(f"[Discovery] Subnet broadcast addresses: {addrs_str}")
