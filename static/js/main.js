// ============================================================
// LAN File Transfer - Frontend Logic
// ============================================================

(function () {
    'use strict';

    // -- State --
    let socket = null;
    let devices = [];
    let selectedDevice = null;
    let selectedPaths = []; // [{path, name, size, is_dir}]
    let transfers = {};     // {task_id: {status, progress, ...}}
    let browserMode = 'file'; // 'file' or 'folder'
    let browserSelected = new Set(); // paths selected in browser

    // -- DOM Elements --
    const selfInfoEl = document.getElementById('selfInfo');
    const deviceListEl = document.getElementById('deviceList');
    const targetInfoEl = document.getElementById('targetInfo');
    const btnSelectFiles = document.getElementById('btnSelectFiles');
    const btnSelectFolder = document.getElementById('btnSelectFolder');
    const btnSend = document.getElementById('btnSend');
    const selectedFilesEl = document.getElementById('selectedFiles');
    const transferListEl = document.getElementById('transferList');
    const dropZone = document.getElementById('dropZone');

    // Browser modal
    const fileBrowserModal = document.getElementById('fileBrowserModal');
    const btnCloseBrowser = document.getElementById('btnCloseBrowser');
    const btnParentDir = document.getElementById('btnParentDir');
    const currentPathEl = document.getElementById('currentPath');
    const browserListEl = document.getElementById('browserList');
    const browserSelectedEl = document.getElementById('browserSelected');
    const btnConfirmSelect = document.getElementById('btnConfirmSelect');
    const browserTitleEl = document.getElementById('browserTitle');

    let currentBrowserPath = '';
    let currentBrowserParent = '';

    // ============================================================
    // Utility
    // ============================================================

    function formatSize(bytes) {
        if (bytes === 0) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
    }

    function formatSpeed(bytesPerSec) {
        return formatSize(bytesPerSec) + '/s';
    }

    // ============================================================
    // WebSocket
    // ============================================================

    function initSocket() {
        socket = io();

        socket.on('connect', function () {
            console.log('WebSocket connected');
        });

        socket.on('device_update', function (data) {
            devices = data;
            renderDevices();
        });

        socket.on('transfer_progress', function (data) {
            updateTransferProgress(data);
        });

        socket.on('transfer_complete', function (data) {
            updateTransferComplete(data);
        });

        socket.on('transfer_error', function (data) {
            updateTransferError(data);
        });

        socket.on('transfer_incoming', function (data) {
            addIncomingTransfer(data);
        });

        socket.on('disconnect', function () {
            console.log('WebSocket disconnected');
        });
    }

    // ============================================================
    // Devices
    // ============================================================

    function fetchDevices() {
        fetch('/api/devices')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                devices = data.devices || [];
                var selfData = data.self || {};
                selfInfoEl.textContent = selfData.hostname + ' (' + selfData.ip + ')';
                renderDevices();
            })
            .catch(function (err) {
                console.error('Failed to fetch devices:', err);
            });
    }

    function renderDevices() {
        if (devices.length === 0) {
            deviceListEl.innerHTML = '<div class="empty-hint">正在搜索设备...</div>';
            return;
        }

        deviceListEl.innerHTML = '';
        devices.forEach(function (device) {
            var div = document.createElement('div');
            div.className = 'device-item';
            if (selectedDevice && selectedDevice.ip === device.ip) {
                div.classList.add('selected');
            }
            div.innerHTML =
                '<div class="device-name">' + escapeHtml(device.hostname) + '</div>' +
                '<div class="device-ip">' + escapeHtml(device.ip) + ':' + device.port + '</div>';
            div.addEventListener('click', function () {
                selectDevice(device);
            });
            deviceListEl.appendChild(div);
        });
    }

    function selectDevice(device) {
        selectedDevice = device;
        renderDevices();

        targetInfoEl.textContent = '目标: ' + device.hostname + ' (' + device.ip + ')';
        targetInfoEl.classList.add('active');

        btnSelectFiles.disabled = false;
        btnSelectFolder.disabled = false;
        updateSendButton();
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ============================================================
    // File Browser
    // ============================================================

    function openBrowser(mode) {
        browserMode = mode;
        browserSelected.clear();
        browserTitleEl.textContent = mode === 'folder' ? '选择文件夹' : '选择文件';
        fileBrowserModal.classList.add('active');
        browsePath('');
    }

    function closeBrowser() {
        fileBrowserModal.classList.remove('active');
    }

    function browsePath(path) {
        var url = '/api/browse';
        if (path) {
            url += '?path=' + encodeURIComponent(path);
        }

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.success) {
                    alert(data.error || 'Failed to browse');
                    return;
                }
                currentBrowserPath = data.current_path;
                currentBrowserParent = data.parent;
                currentPathEl.textContent = data.current_path;
                renderBrowserEntries(data.entries);
            })
            .catch(function (err) {
                console.error('Browse error:', err);
            });
    }

    function renderBrowserEntries(entries) {
        browserListEl.innerHTML = '';

        entries.forEach(function (entry) {
            var div = document.createElement('div');
            div.className = 'browser-entry';
            if (browserSelected.has(entry.path)) {
                div.classList.add('selected');
            }

            var icon = entry.is_dir ? '📁' : '📄';
            var sizeStr = entry.is_dir ? '' : formatSize(entry.size);

            var canSelect = browserMode === 'folder' ? entry.is_dir : !entry.is_dir;

            div.innerHTML =
                (canSelect ? '<input type="checkbox" class="entry-checkbox" ' +
                    (browserSelected.has(entry.path) ? 'checked' : '') + '>' : '<span style="width:16px"></span>') +
                '<span class="entry-icon">' + icon + '</span>' +
                '<span class="entry-name">' + escapeHtml(entry.name) + '</span>' +
                '<span class="entry-size">' + sizeStr + '</span>';

            div.addEventListener('click', function (e) {
                if (e.target.classList.contains('entry-checkbox')) {
                    toggleBrowserSelect(entry);
                    return;
                }

                if (entry.is_dir) {
                    if (browserMode === 'folder') {
                        // In folder mode, clicking navigates; checkbox selects
                        browsePath(entry.path);
                    } else {
                        browsePath(entry.path);
                    }
                } else {
                    if (browserMode === 'file') {
                        toggleBrowserSelect(entry);
                    }
                }
            });

            // Double click to enter directory in folder mode
            if (entry.is_dir && browserMode === 'folder') {
                div.addEventListener('dblclick', function () {
                    browsePath(entry.path);
                });
            }

            browserListEl.appendChild(div);
        });

        updateBrowserSelectedCount();
    }

    function toggleBrowserSelect(entry) {
        if (browserSelected.has(entry.path)) {
            browserSelected.delete(entry.path);
        } else {
            browserSelected.add(entry.path);
        }
        // Re-render to update checkboxes
        var entries = [];
        browserListEl.querySelectorAll('.browser-entry').forEach(function (el) {
            // Just re-browse to refresh
        });
        browsePath(currentBrowserPath);
    }

    function updateBrowserSelectedCount() {
        browserSelectedEl.textContent = '已选: ' + browserSelected.size + ' 项';
    }

    function confirmBrowserSelect() {
        if (browserSelected.size === 0) {
            // If folder mode and nothing selected, select current folder
            if (browserMode === 'folder' && currentBrowserPath) {
                var pathParts = currentBrowserPath.split('/');
                var folderName = pathParts[pathParts.length - 1] || currentBrowserPath;
                selectedPaths.push({
                    path: currentBrowserPath,
                    name: folderName,
                    size: 0,
                    is_dir: true,
                });
            }
        } else {
            browserSelected.forEach(function (p) {
                // Avoid duplicates
                var exists = selectedPaths.some(function (sp) { return sp.path === p; });
                if (!exists) {
                    var parts = p.split('/');
                    var name = parts[parts.length - 1] || p;
                    selectedPaths.push({
                        path: p,
                        name: name,
                        size: 0,
                        is_dir: browserMode === 'folder',
                    });
                }
            });
        }

        closeBrowser();
        renderSelectedFiles();
        updateSendButton();
    }

    // ============================================================
    // Selected Files
    // ============================================================

    function renderSelectedFiles() {
        if (selectedPaths.length === 0) {
            selectedFilesEl.innerHTML = '';
            return;
        }

        selectedFilesEl.innerHTML = '';
        selectedPaths.forEach(function (file, index) {
            var div = document.createElement('div');
            div.className = 'selected-file-item';
            var icon = file.is_dir ? '📁' : '📄';
            div.innerHTML =
                '<span class="file-name">' + icon + ' ' + escapeHtml(file.name) + '</span>' +
                '<button class="btn-remove" data-index="' + index + '">&times;</button>';
            div.querySelector('.btn-remove').addEventListener('click', function () {
                selectedPaths.splice(index, 1);
                renderSelectedFiles();
                updateSendButton();
            });
            selectedFilesEl.appendChild(div);
        });
    }

    function updateSendButton() {
        btnSend.disabled = !selectedDevice || selectedPaths.length === 0;
    }

    // ============================================================
    // Send Files
    // ============================================================

    function sendFiles() {
        if (!selectedDevice || selectedPaths.length === 0) return;

        var paths = selectedPaths.map(function (f) { return f.path; });

        fetch('/api/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target_ip: selectedDevice.ip,
                target_port: selectedDevice.port,
                paths: paths,
            }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    addSendTransfer(data.task_id, selectedDevice, selectedPaths);
                    selectedPaths = [];
                    renderSelectedFiles();
                    updateSendButton();
                } else {
                    alert('传输失败: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(function (err) {
                alert('请求失败: ' + err.message);
            });
    }

    // ============================================================
    // Transfer Log
    // ============================================================

    function addSendTransfer(taskId, device, files) {
        transfers[taskId] = {
            task_id: taskId,
            direction: 'send',
            target: device.hostname,
            files_count: files.length,
            status: 'sending',
            progress: 0,
            speed: 0,
        };
        renderTransfers();
    }

    function addIncomingTransfer(data) {
        transfers[data.task_id] = {
            task_id: data.task_id,
            direction: 'receive',
            target: data.from_ip,
            files_count: data.files_count,
            total_size: data.total_size,
            status: 'receiving',
            progress: 0,
            speed: 0,
        };
        renderTransfers();
    }

    function updateTransferProgress(data) {
        var t = transfers[data.task_id];
        if (!t) {
            // Unknown transfer, create entry
            t = {
                task_id: data.task_id,
                direction: 'unknown',
                target: '',
                status: 'sending',
                progress: 0,
                speed: 0,
            };
            transfers[data.task_id] = t;
        }
        t.progress = data.progress_percent || 0;
        t.speed = data.speed || 0;
        t.current_file = data.file || '';
        renderTransfers();
    }

    function updateTransferComplete(data) {
        var t = transfers[data.task_id];
        if (t) {
            t.status = 'completed';
            t.progress = 100;
        } else {
            transfers[data.task_id] = {
                task_id: data.task_id,
                status: 'completed',
                progress: 100,
                files_count: data.files_count,
                total_size: data.total_size,
            };
        }
        renderTransfers();
    }

    function updateTransferError(data) {
        var t = transfers[data.task_id];
        if (t) {
            t.status = 'error';
            t.error = data.error_message;
        } else {
            transfers[data.task_id] = {
                task_id: data.task_id,
                status: 'error',
                error: data.error_message,
                progress: 0,
            };
        }
        renderTransfers();
    }

    function renderTransfers() {
        var taskIds = Object.keys(transfers);

        if (taskIds.length === 0) {
            transferListEl.innerHTML = '<div class="empty-hint">暂无传输记录</div>';
            return;
        }

        transferListEl.innerHTML = '';

        // Show newest first
        taskIds.reverse().forEach(function (taskId) {
            var t = transfers[taskId];
            var div = document.createElement('div');
            div.className = 'transfer-item';

            var dirIcon = t.direction === 'send' ? '⬆️' : (t.direction === 'receive' ? '⬇️' : '🔄');
            var statusLabel = t.status === 'sending' ? '发送中' :
                t.status === 'receiving' ? '接收中' :
                    t.status === 'completed' ? '已完成' :
                        t.status === 'error' ? '失败' : t.status;

            var progressClass = t.status === 'completed' ? 'completed' : '';

            var detailLeft = t.current_file ? escapeHtml(t.current_file) : (t.files_count ? t.files_count + ' 个文件' : '');
            var detailRight = '';
            if (t.status === 'sending' || t.status === 'receiving') {
                detailRight = t.speed ? formatSpeed(t.speed) : '';
            } else if (t.status === 'completed' && t.total_size) {
                detailRight = formatSize(t.total_size);
            } else if (t.status === 'error') {
                detailRight = t.error || '';
            }

            div.innerHTML =
                '<div class="transfer-header">' +
                '  <span class="transfer-task-name">' + dirIcon + ' ' + (t.target || '') + '</span>' +
                '  <span class="transfer-status ' + t.status + '">' + statusLabel + '</span>' +
                '</div>' +
                '<div class="progress-bar-container">' +
                '  <div class="progress-bar ' + progressClass + '" style="width: ' + (t.progress || 0) + '%"></div>' +
                '</div>' +
                '<div class="transfer-details">' +
                '  <span>' + detailLeft + '</span>' +
                '  <span>' + escapeHtml(detailRight) + '</span>' +
                '</div>';

            transferListEl.appendChild(div);
        });
    }

    // ============================================================
    // Event Binding
    // ============================================================

    function bindEvents() {
        btnSelectFiles.addEventListener('click', function () {
            openBrowser('file');
        });

        btnSelectFolder.addEventListener('click', function () {
            openBrowser('folder');
        });

        btnSend.addEventListener('click', sendFiles);

        btnCloseBrowser.addEventListener('click', closeBrowser);

        btnParentDir.addEventListener('click', function () {
            if (currentBrowserParent) {
                browsePath(currentBrowserParent);
            }
        });

        btnConfirmSelect.addEventListener('click', confirmBrowserSelect);

        // Close modal on backdrop click
        fileBrowserModal.addEventListener('click', function (e) {
            if (e.target === fileBrowserModal) {
                closeBrowser();
            }
        });

        // Keyboard
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                closeBrowser();
            }
        });
    }

    // ============================================================
    // Init
    // ============================================================

    function init() {
        initSocket();
        fetchDevices();
        bindEvents();

        // Periodically refresh device list (match broadcast interval)
        setInterval(fetchDevices, 3000);
    }

    // Start
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
