/* WolfRAT Mobile Web UI — Client JS */

let ws = null;
let reconnectTimer = null;
let selectedPlayer = null;
let authToken = localStorage.getItem('wolfauth') || sessionStorage.getItem('wolfauth');
let savedUser = localStorage.getItem('wolfuser') || '';
let savedToken = localStorage.getItem('wolftoken') || '';
let pendingChatTexts = [];
let operationResultTimer = null;

function operationResultSurface() {
    let surface = document.getElementById('operation-result');
    if (surface) return surface;

    surface = document.createElement('div');
    surface.id = 'operation-result';
    surface.setAttribute('role', 'status');
    surface.setAttribute('aria-live', 'polite');
    Object.assign(surface.style, {
        position: 'fixed',
        top: '58px',
        left: '12px',
        right: '12px',
        zIndex: '300',
        padding: '10px 14px',
        borderRadius: '8px',
        border: '1px solid #555',
        background: '#171717',
        color: '#e8e8d0',
        fontSize: '10pt',
        fontWeight: '600',
        boxShadow: '0 4px 16px rgba(0,0,0,0.7)',
    });
    document.body.appendChild(surface);
    return surface;
}

function renderOperationResult(data, context = {}) {
    const result = data && typeof data === 'object' ? data : {};
    let outcome = 'error';
    if (result.accepted === true) {
        // The server took the command. A read-back that did not confirm it is
        // its own outcome: the server did not reject anything.
        if (result.verified === true) outcome = 'verified';
        else if (result.verified === undefined && result.ok !== false) outcome = 'accepted';
        else outcome = 'unverified';
    } else if (result.accepted === false || result.ok === false) {
        outcome = 'rejected';
    } else if (result.ok === true) {
        outcome = 'accepted';
    }

    const labels = {
        accepted: 'Accepted',
        rejected: 'Rejected',
        unverified: 'Unverified',
        error: 'Error',
        verified: 'Verified',
    };
    const colors = {
        accepted: ['#173517', '#50ff50'],
        rejected: ['#3a1a1a', '#ff6040'],
        unverified: ['#38300f', '#ffc840'],
        error: ['#3a1a1a', '#ff6040'],
        verified: ['#182c38', '#60c8ff'],
    };
    const subject = context.label
        || result.command
        || (result.action ? `${result.action} player ${result.pid || ''}`.trim() : '')
        || (result.type === 'chat_result' ? 'Server chat' : 'Server operation');
    const replies = Array.isArray(result.replies) ? result.replies.filter(Boolean) : [];
    const unverified = (
        result.accepted === true
        && result.ok === false
        && result.verified !== true
    );
    const detail = result.verification_error
        || result.error
        || (unverified ? 'sent, but the read-back did not confirm it' : '')
        || replies[replies.length - 1]
        || subject;
    const message = detail === subject
        ? `${labels[outcome]}: ${subject}`
        : `${labels[outcome]}: ${subject} — ${detail}`;

    const surface = operationResultSurface();
    surface.dataset.outcome = outcome;
    surface.textContent = message;
    surface.style.background = colors[outcome][0];
    surface.style.color = colors[outcome][1];
    surface.style.borderColor = colors[outcome][1];
    surface.style.display = 'block';
    clearTimeout(operationResultTimer);
    operationResultTimer = setTimeout(() => { surface.style.display = 'none'; }, 8000);
    return { ...result, outcome };
}

function handleWebSocketMessage(data) {
    if (data.type === 'state') {
        updateUI(data);
    } else if (data.type === 'chat') {
        updateChat(data.chat || []);
    } else if (data.type === 'error' || String(data.type || '').endsWith('_result')) {
        const result = renderOperationResult(data);
        if (
            data.type === 'chat_result'
            && pendingChatTexts.length
        ) {
            const submittedText = pendingChatTexts.shift();
            const input = document.getElementById('chat-input');
            if (
                (result.outcome === 'accepted' || result.outcome === 'verified')
                && input.value.trim() === submittedText
            ) {
                input.value = '';
            }
        }
    }
}

async function submitHttpOperation(path, body, context = {}) {
    try {
        const response = await fetch(path, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + authToken,
            },
            body: JSON.stringify(body),
        });
        let data;
        try {
            data = await response.json();
        } catch (error) {
            data = { error: `Invalid server response (HTTP ${response.status})` };
        }
        if (!response.ok) {
            data = {
                ...data,
                ok: undefined,
                accepted: undefined,
                verified: undefined,
                error: data.error || `HTTP ${response.status}`,
            };
        }
        return renderOperationResult(data, context);
    } catch (error) {
        return renderOperationResult(
            { error: error && error.message ? error.message : 'Connection error' },
            context,
        );
    }
}

// --- Login ---
function doLogin(e) {
    e.preventDefault();
    const user = document.getElementById('login-user').value.trim();
    const token = document.getElementById('login-pass').value.trim();
    const remember = document.getElementById('login-remember').checked;
    if (!user || !token) return false;

    fetch('/api/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user, token: token })
    })
    .then(r => r.json())
    .then(data => {
        if (data.ok) {
            authToken = data.token;
            if (remember) {
                localStorage.setItem('wolfauth', authToken);
                localStorage.setItem('wolfuser', user);
                localStorage.setItem('wolftoken', token);
            } else {
                sessionStorage.setItem('wolfauth', authToken);
                localStorage.removeItem('wolfauth');
                localStorage.removeItem('wolfuser');
                localStorage.removeItem('wolftoken');
            }
            document.getElementById('login-overlay').classList.add('hidden');
            document.getElementById('conn-overlay').classList.remove('hidden');
            connectWS();
        } else {
            const err = document.getElementById('login-error');
            err.textContent = data.error || 'Login failed';
            err.classList.remove('hidden');
        }
    })
    .catch(() => {
        const err = document.getElementById('login-error');
        err.textContent = 'Connection error';
        err.classList.remove('hidden');
    });
    return false;
}

// On load: check if already logged in
if (authToken) {
    document.getElementById('login-overlay').classList.add('hidden');
    document.getElementById('conn-overlay').classList.remove('hidden');
    connectWS();
} else if (savedUser && savedToken) {
    // Pre-fill saved credentials
    document.getElementById('login-user').value = savedUser;
    document.getElementById('login-pass').value = savedToken;
    document.getElementById('login-remember').checked = true;
}

// --- Tab navigation ---
document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + tab).classList.add('active');
    });
});

// --- WebSocket ---
// A browser never reveals why a WebSocket handshake failed, so ask the API.
async function tokenWasRejected() {
    try {
        const response = await fetch('/api/status', {
            headers: { 'Authorization': 'Bearer ' + authToken },
        });
        return response.status === 401;
    } catch (error) {
        return false;  // WolfRAT itself is unreachable; keep retrying
    }
}

function showLogin(message) {
    authToken = null;
    localStorage.removeItem('wolfauth');
    sessionStorage.removeItem('wolfauth');
    if (ws) { ws.onclose = null; try { ws.close(); } catch (error) {} ws = null; }
    document.getElementById('conn-overlay').classList.add('hidden');
    document.getElementById('app').classList.add('hidden');
    document.getElementById('login-overlay').classList.remove('hidden');
    const err = document.getElementById('login-error');
    err.textContent = message;
    err.classList.remove('hidden');
}

function connectWS() {
    if (!authToken) return;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + location.host + '/ws?token=' + encodeURIComponent(authToken));

    ws.onopen = () => {
        document.getElementById('conn-overlay').classList.add('fade-out');
        setTimeout(() => {
            document.getElementById('conn-overlay').style.display = 'none';
            document.getElementById('app').classList.remove('hidden');
        }, 500);
        document.getElementById('conn-dot').className = 'conn-dot connected';
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        } catch (e) {}
    };

    ws.onclose = () => {
        document.getElementById('conn-dot').className = 'conn-dot disconnected';
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(async () => {
            reconnectTimer = null;
            if (await tokenWasRejected()) {
                showLogin('Access token no longer valid - log in again');
                return;
            }
            connectWS();
        }, 3000);
    };

    ws.onerror = () => {};
}

// --- UI Update ---
function updateUI(state) {
    // Connection status
    const dot = document.getElementById('conn-dot');
    dot.className = 'conn-dot ' + (state.connected ? 'connected' : 'disconnected');

    // Player badge
    document.getElementById('player-badge').textContent = state.player_count;

    // Dashboard
    document.getElementById('dash-status').textContent = state.connected ? 'Online' : 'Offline';
    document.getElementById('dash-status').style.color = state.connected ? '#50ff50' : '#ff4040';
    document.getElementById('dash-players').textContent = state.player_count;
    document.getElementById('dash-mode').textContent = state.game_mode || '-';

    // Chat box: the server says how long one line may be (51, or 110 with long chat).
    if (Number(state.chat_max) > 0) {
        document.getElementById('chat-input').maxLength = Number(state.chat_max);
    }

    // Dashboard mini-chat
    renderMiniChat(state.chat || []);

    // Players
    renderPlayers(state.players || []);

    // Chat
    renderChat(state.chat || []);

    // Maps
    renderMaps(state.missions || []);
}

// --- Chat-only update (from dedicated chat broadcast) ---
function updateChat(messages) {
    renderChat(messages);
    renderMiniChat(messages);
}

// --- Render functions ---
function renderMiniChat(messages) {
    const el = document.getElementById('dash-chat');
    if (!messages.length) { el.innerHTML = '<p class="empty">No chat yet</p>'; return; }
    const recent = messages.slice(-8);
    el.innerHTML = recent.map(m =>
        `<div class="chat-line"><span class="chat-time">${esc(m.time)}</span><span class="chat-text">${esc(m.text)}</span></div>`
    ).join('');
    el.scrollTop = el.scrollHeight;
}

function renderPlayers(players) {
    const el = document.getElementById('player-list');
    document.getElementById('player-count').textContent = `(${players.length})`;
    if (!players.length) { el.innerHTML = '<p class="empty">No players connected</p>'; return; }
    el.innerHTML = players.map(p => {
        const playerId = Number(p.id);
        const playerName = String(p.name || '');
        if (!Number.isInteger(playerId) || !playerName) return '';
        const teamClass = p.team === '1' ? 'team-1' : p.team === '2' ? 'team-2' : '';
        const teamLabel = p.team_name || p.team || '?';
        return `<div class="player-item"
            data-player-id="${playerId}"
            data-player-name="${esc(playerName)}"
            onclick="openActionSheetFromElement(this)">
            <div class="player-info">
                <div class="player-name">${esc(playerName)}</div>
                <div class="player-meta">
                    <span class="team-badge ${teamClass}">${esc(teamLabel)}</span>
                    ${p.class ? ' · ' + esc(p.class) : ''}
                    ${p.ping && p.ping !== '-' ? ' · ' + esc(p.ping) + 'ms' : ''}
                </div>
            </div>
            <div class="player-score">${esc(p.kills || '0')}</div>
        </div>`;
    }).join('');
}

function renderChat(messages) {
    const el = document.getElementById('chat-messages');
    if (!messages.length) { el.innerHTML = '<p class="empty">No messages</p>'; return; }
    el.innerHTML = messages.map(m =>
        `<div class="chat-line"><span class="chat-time">${esc(m.time)}</span><span class="chat-text">${esc(m.text)}</span></div>`
    ).join('');
    el.scrollTop = el.scrollHeight;
}

function renderMaps(missions) {
    const el = document.getElementById('map-list');
    if (!missions.length) { el.innerHTML = '<p class="empty">No maps loaded</p>'; return; }
    el.innerHTML = missions.map(mission => {
        const queueIndex = Number(mission.queue_index);
        const filename = String(mission.filename || '');
        const missionName = String(
            mission.display_name
            || filename.replace(/\.(bms|npj|npz)$/i, '')
        );
        if (!Number.isInteger(queueIndex) || !filename) return '';
        return `<div class="map-item ${mission.is_current ? 'current' : ''}"
            data-queue-index="${queueIndex}"
            data-filename="${esc(filename)}"
            onclick="switchMapFromElement(this)">
            <span class="map-index">${queueIndex}</span>${esc(missionName)}
        </div>`;
    }).join('');
}

// --- Actions ---
function quickAction(action, label) {
    return submitHttpOperation(
        '/api/action',
        { action },
        { label },
    );
}

function sendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    const sendMsg = '[ADMIN] ' + msg;
    if (ws && ws.readyState === WebSocket.OPEN) {
        pendingChatTexts.push(msg);
        ws.send(JSON.stringify({ type: 'chat', message: sendMsg }));
    } else {
        return submitHttpOperation(
            '/api/chat/send',
            { message: sendMsg },
            { label: 'Server chat' },
        ).then(result => {
            if (
                result.outcome === 'accepted'
                || result.outcome === 'verified'
            ) {
                if (input.value.trim() === msg) input.value = '';
            }
            return result;
        });
    }
}

// Enter key to send chat
document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendChat();
});

function switchMapFromElement(element) {
    return switchMap(
        Number(element.dataset.queueIndex),
        element.dataset.filename,
    );
}

function switchMap(queueIndex, filename) {
    if (!confirm('Switch to ' + filename + '?')) return;
    return submitHttpOperation(
        '/api/map/switch',
        { index: queueIndex, map: filename },
        { label: `Switch to ${filename}` },
    );
}

// --- Player action sheet ---
function openActionSheetFromElement(element) {
    return openActionSheet(
        element.dataset.playerId,
        element.dataset.playerName,
    );
}

function openActionSheet(pid, name) {
    selectedPlayer = { pid, name };
    document.getElementById('action-player-name').textContent = name;
    document.getElementById('action-sheet').classList.remove('hidden');
}

function closeActionSheet() {
    document.getElementById('action-sheet').classList.add('hidden');
    selectedPlayer = null;
}

function playerAction(action) {
    if (!selectedPlayer) return;
    const target = { ...selectedPlayer };
    const name = target.name;
    const confirmActions = { kick: 'Kick ' + name + '?', ban: 'BAN ' + name + '?' };
    if (confirmActions[action] && !confirm(confirmActions[action])) return;

    return submitHttpOperation(
        '/api/player/action',
        {
            pid: target.pid,
            name: target.name,
            action,
        },
        { label: `${action} ${target.name}` },
    ).then(result => {
        if (
            (result.outcome === 'accepted' || result.outcome === 'verified')
            && selectedPlayer
            && selectedPlayer.pid === target.pid
            && selectedPlayer.name === target.name
        ) {
            closeActionSheet();
        }
        return result;
    });
}

// Close action sheet on background tap
document.getElementById('action-sheet').addEventListener('click', (e) => {
    if (e.target === document.getElementById('action-sheet')) closeActionSheet();
});

// --- Utility ---
function esc(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

