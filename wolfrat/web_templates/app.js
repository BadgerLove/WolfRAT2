/* WolfRAT Mobile Web UI — Client JS */

let ws = null;
let reconnectTimer = null;
let selectedPlayer = null;
let authToken = localStorage.getItem('wolfauth') || sessionStorage.getItem('wolfauth');
let savedUser = localStorage.getItem('wolfuser') || '';
let savedToken = localStorage.getItem('wolftoken') || '';

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
function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + location.host + '/ws?token=' + encodeURIComponent(authToken));

    ws.onopen = () => {
        document.getElementById('conn-overlay').classList.add('fade-out');
        setTimeout(() => {
            document.getElementById('conn-overlay').style.display = 'none';
            document.getElementById('app').classList.remove('hidden');
        }, 500);
        document.getElementById('conn-dot').className = 'conn-dot connected';
        if (reconnectTimer) { clearInterval(reconnectTimer); reconnectTimer = null; }
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'state') updateUI(data);
            else if (data.type === 'chat') updateChat(data.chat || []);
        } catch (e) {}
    };

    ws.onclose = () => {
        document.getElementById('conn-dot').className = 'conn-dot disconnected';
        if (!reconnectTimer) {
            reconnectTimer = setInterval(() => {
                connectWS();
            }, 3000);
        }
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
    document.getElementById('dash-mode').textContent = state.game_mode || '—';

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
        const teamClass = p.team === '1' ? 'team-1' : p.team === '2' ? 'team-2' : '';
        const teamLabel = p.team_name || p.team || '?';
        return `<div class="player-item" onclick="openActionSheet('${esc(p.id)}', '${esc(p.name)}')">
            <div class="player-info">
                <div class="player-name">${esc(p.name)}</div>
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
    el.innerHTML = missions.map((m, i) => {
        const isCurrent = m.includes('<CURRENT MISSION>');
        // Strip tags
        let clean = m.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
        // Strip index prefix ("0: ")
        const colonIdx = clean.indexOf(':');
        if (colonIdx > -1 && colonIdx < 5) clean = clean.substring(colonIdx + 1).trim();
        // Extract filename (first token ending in .bms/.npj/.npaj)
        const extMatch = clean.match(/(\S+\.(?:bms|npj|npaj))/i);
        const filename = extMatch ? extMatch[1] : clean.split(' - ')[0].split(' ')[0];
        // Extract display name: text before the filename, or description after extension
        let missionName = clean;
        if (extMatch) {
            const idx = clean.indexOf(extMatch[1]);
            const before = idx > 0 ? clean.substring(0, idx).trim() : '';
            if (before && before !== filename) {
                missionName = before.replace(/-\s*$/, '').trim();
            } else {
                // Fallback: strip extension
                missionName = filename.replace(/\.(bms|npj|npaj)$/i, '');
            }
        }
        return `<div class="map-item ${isCurrent ? 'current' : ''}" onclick="switchMap('${esc(filename)}')">
            <span class="map-index">${i + 1}</span>${esc(missionName)}
        </div>`;
    }).join('');
}

// --- Actions ---
function sendCommand(cmd) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'command', command: cmd }));
    } else {
        fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
            body: JSON.stringify({ command: cmd })
        });
    }
}

function sendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    const sendMsg = '[ADMIN] ' + msg;
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'chat', message: sendMsg }));
    } else {
        fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
            body: JSON.stringify({ message: sendMsg })
        });
    }
    input.value = '';
}

// Enter key to send chat
document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendChat();
});

function switchMap(filename) {
    if (!confirm('Switch to ' + filename + '?')) return;
    fetch('/api/map/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
        body: JSON.stringify({ map: filename })
    })
    .then(r => r.json())
    .then(data => {
        if (!data.ok) alert('Error: ' + (data.error || 'Unknown error'));
    })
    .catch(() => alert('Connection error'));
}

// --- Player action sheet ---
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
    const name = selectedPlayer.name;
    const confirmActions = { kick: 'Kick ' + name + '?', ban: 'BAN ' + name + '?' };
    if (confirmActions[action] && !confirm(confirmActions[action])) return;

    fetch('/api/player/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
        body: JSON.stringify({ pid: selectedPlayer.pid, action })
    });
    closeActionSheet();
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

// --- Init ---
connectWS();
