/* ── Sunny Osprey Dashboard — Real-time JS ─────────────────────────────────── */
/* WebSocket connection, event rendering, live feed, stats management */

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  ws: null,
  events: [],
  filter: 'all',
  reconnectAttempts: 0,
  maxReconnectAttempts: 20,
  reconnectDelay: 2000,
  connected: false,
};

// ── DOM References ────────────────────────────────────────────────────────────
const dom = {
  statusDot:          () => document.getElementById('statusDot'),
  statusLabel:        () => document.getElementById('statusLabel'),
  cameraFeed:         () => document.getElementById('cameraFeed'),
  cameraPlaceholder:  () => document.getElementById('cameraPlaceholder'),
  analyzingBadge:     () => document.getElementById('analyzingBadge'),
  eventsList:         () => document.getElementById('eventsList'),
  eventsEmpty:        () => document.getElementById('eventsEmpty'),
  totalEvents:        () => document.getElementById('totalEvents'),
  suspiciousEvents:   () => document.getElementById('suspiciousEvents'),
  normalEvents:       () => document.getElementById('normalEvents'),
  activeConnections:  () => document.getElementById('activeConnections'),
  alertRateFill:      () => document.getElementById('alertRateFill'),
  alertRateLabel:     () => document.getElementById('alertRateLabel'),
  timeDisplay:        () => document.getElementById('timeDisplay'),
  toast:              () => document.getElementById('toast'),
  modalBackdrop:      () => document.getElementById('modalBackdrop'),
  modalTitle:         () => document.getElementById('modalTitle'),
  modalBody:          () => document.getElementById('modalBody'),
};

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectWebSocket() {
  const wsUrl = `ws://${window.location.host}/ws`;
  setStatus('connecting', 'Connecting...');

  try {
    state.ws = new WebSocket(wsUrl);
  } catch (err) {
    console.error('WS creation error:', err);
    scheduleReconnect();
    return;
  }

  state.ws.onopen = () => {
    state.connected = true;
    state.reconnectAttempts = 0;
    setStatus('connected', 'Connected');
    console.info('[WS] Connected');

    // Heartbeat every 25 seconds
    state.heartbeat = setInterval(() => {
      if (state.ws?.readyState === WebSocket.OPEN) {
        state.ws.send('ping');
      }
    }, 25000);
  };

  state.ws.onclose = () => {
    state.connected = false;
    clearInterval(state.heartbeat);
    setStatus('error', 'Disconnected');
    console.warn('[WS] Disconnected');
    scheduleReconnect();
  };

  state.ws.onerror = (err) => {
    console.error('[WS] Error:', err);
    setStatus('error', 'Connection error');
  };

  state.ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      handleMessage(msg);
    } catch (err) {
      console.warn('[WS] Parse error:', err);
    }
  };
}

function scheduleReconnect() {
  if (state.reconnectAttempts >= state.maxReconnectAttempts) {
    setStatus('error', 'Cannot connect — is the dashboard running?');
    return;
  }
  state.reconnectAttempts++;
  const delay = Math.min(state.reconnectDelay * state.reconnectAttempts, 15000);
  setStatus('connecting', `Reconnecting in ${(delay/1000).toFixed(0)}s... (${state.reconnectAttempts})`);
  setTimeout(connectWebSocket, delay);
}

// ── Message Handler ───────────────────────────────────────────────────────────

function handleMessage(msg) {
  switch (msg.type) {
    case 'init':
      // Initialize with historical events
      if (msg.events) {
        state.events = msg.events;
        renderAllEvents();
      }
      if (msg.stats) updateStats(msg.stats);
      if (msg.frame) updateCameraFeed(msg.frame);
      break;

    case 'new_event':
      addEvent(msg.event);
      if (msg.stats) updateStats(msg.stats);
      break;

    case 'frame':
      updateCameraFeed(msg.frame);
      break;

    case 'clear':
      // Server cleared all events — reset UI
      state.events = [];
      renderAllEvents();
      if (msg.stats) updateStats(msg.stats);
      showToast('🗑️ History cleared — fresh session', 'ok');
      break;

    case 'ping':
      // Server keepalive — no action needed
      break;

    default:
      console.debug('[WS] Unknown message type:', msg.type);
  }
}

// ── Camera Feed ───────────────────────────────────────────────────────────────

function updateCameraFeed(frameB64) {
  if (!frameB64) return;
  const feed = dom.cameraFeed();
  const placeholder = dom.cameraPlaceholder();

  feed.src = `data:image/jpeg;base64,${frameB64}`;
  feed.classList.add('visible');
  placeholder.classList.add('hidden');
}

// ── Stats Update ──────────────────────────────────────────────────────────────

function updateStats(stats) {
  animateCounter(dom.totalEvents(), stats.total_events ?? 0);
  animateCounter(dom.suspiciousEvents(), stats.suspicious_events ?? 0);
  animateCounter(dom.normalEvents(), stats.normal_events ?? 0);

  if (stats.active_connections !== undefined) {
    dom.activeConnections().textContent = stats.active_connections;
  }

  // Alert rate gauge
  const total = stats.total_events || 0;
  const suspicious = stats.suspicious_events || 0;
  const rate = total > 0 ? (suspicious / total) * 100 : 0;
  dom.alertRateFill().style.width = `${Math.min(rate, 100)}%`;
  dom.alertRateLabel().textContent = `${rate.toFixed(1)}%`;
}

function animateCounter(el, target) {
  if (!el) return;
  const current = parseInt(el.textContent) || 0;
  if (current === target) return;

  const step = target > current ? 1 : -1;
  const speed = Math.max(16, Math.min(100, Math.abs(target - current) * 10));
  let val = current;

  const interval = setInterval(() => {
    val += step;
    el.textContent = val;
    if (val === target) clearInterval(interval);
  }, speed);
}

// ── Event Rendering ───────────────────────────────────────────────────────────

function addEvent(event) {
  state.events.unshift(event);
  if (state.events.length > 100) state.events.pop();

  // Show analysis indicator briefly
  const badge = dom.analyzingBadge();
  badge.classList.remove('hidden');
  setTimeout(() => badge.classList.add('hidden'), 3000);

  // Render the new card at top
  const card = createEventCard(event);
  const list = dom.eventsList();
  const empty = dom.eventsEmpty();

  if (empty) empty.remove();

  list.insertBefore(card, list.firstChild);

  // Apply current filter
  applyFilter(card, event);

  // Alert notification
  if (event.suspicious?.toLowerCase() === 'yes') {
    showToast(`🚨 Suspicious activity detected!`, 'alert');
    document.body.classList.add('body-alert');
    setTimeout(() => document.body.classList.remove('body-alert'), 700);
  } else {
    showToast(`✅ Normal activity — event recorded`, 'ok');
  }
}

function renderAllEvents() {
  const list = dom.eventsList();
  list.innerHTML = '';

  if (state.events.length === 0) {
    list.innerHTML = `
      <div class="events-empty" id="eventsEmpty">
        <div class="empty-icon">👁️</div>
        <p>Monitoring in progress...</p>
        <p class="empty-sub">Events will appear here when motion is detected</p>
      </div>`;
    return;
  }

  state.events.forEach(event => {
    const card = createEventCard(event);
    list.appendChild(card);
    applyFilter(card, event);
  });
}

function createEventCard(event) {
  const isSuspicious = event.suspicious?.toLowerCase() === 'yes';
  const ts = event.timestamp ? formatTime(event.timestamp) : 'Unknown time';
  const description = event.description || event.error || 'No description available';
  const eventId = event.event_id || 'unknown';

  const card = document.createElement('div');
  card.className = `event-card event-card--${isSuspicious ? 'suspicious' : 'normal'}`;
  card.dataset.suspicious = isSuspicious ? 'true' : 'false';
  card.dataset.eventId = eventId;

  card.innerHTML = `
    <div class="event-card-header">
      <div class="event-badge event-badge--${isSuspicious ? 'suspicious' : 'normal'}">
        ${isSuspicious ? '🚨 Suspicious' : '✅ Normal'}
      </div>
      <span class="event-time">${ts}</span>
    </div>
    <div class="event-id">#${eventId}</div>
    <div class="event-description">${escapeHtml(description)}</div>
  `;

  card.addEventListener('click', () => showEventModal(event));
  return card;
}

function applyFilter(card, event) {
  const isSuspicious = event.suspicious?.toLowerCase() === 'yes';
  const show = (
    state.filter === 'all' ||
    (state.filter === 'suspicious' && isSuspicious) ||
    (state.filter === 'normal' && !isSuspicious)
  );
  card.classList.toggle('filtered', !show);
}

// ── Filter Controls ───────────────────────────────────────────────────────────

window.setFilter = function(filter) {
  state.filter = filter;
  ['all', 'suspicious', 'normal'].forEach(f => {
    document.getElementById(`filter${f.charAt(0).toUpperCase() + f.slice(1)}`)
      ?.classList.toggle('active', f === filter);
  });

  // Re-apply filter to all cards
  document.querySelectorAll('.event-card').forEach(card => {
    const isSuspicious = card.dataset.suspicious === 'true';
    const show = (
      filter === 'all' ||
      (filter === 'suspicious' && isSuspicious) ||
      (filter === 'normal' && !isSuspicious)
    );
    card.classList.toggle('filtered', !show);
  });
};

// ── Event Modal ───────────────────────────────────────────────────────────────

function showEventModal(event) {
  const isSuspicious = event.suspicious?.toLowerCase() === 'yes';
  const ts = event.timestamp ? new Date(event.timestamp).toLocaleString() : 'Unknown';

  dom.modalTitle().textContent = isSuspicious ? '🚨 Suspicious Activity Detected' : '✅ Normal Activity';

  dom.modalBody().innerHTML = `
    <div class="modal-detail-row">
      <span class="modal-detail-label">Event ID</span>
      <span class="modal-detail-value" style="font-family: 'JetBrains Mono'; font-size:12px">${event.event_id || 'N/A'}</span>
    </div>
    <div class="modal-detail-row">
      <span class="modal-detail-label">Status</span>
      <span class="modal-detail-value" style="color: ${isSuspicious ? 'var(--accent-red)' : 'var(--accent-green)'}; font-weight: 600">
        ${isSuspicious ? '🚨 SUSPICIOUS' : '✅ NORMAL'}
      </span>
    </div>
    <div class="modal-detail-row">
      <span class="modal-detail-label">Timestamp</span>
      <span class="modal-detail-value">${ts}</span>
    </div>
    <div class="modal-detail-row">
      <span class="modal-detail-label">Source</span>
      <span class="modal-detail-value">${event.source || 'webcam'}</span>
    </div>
    <div style="margin-top: 16px; font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.6px; font-weight: 600; margin-bottom: 8px;">
      AI Analysis
    </div>
    <div class="modal-description">${escapeHtml(event.description || 'No description available.')}</div>
    ${event.error ? `<div style="margin-top:12px; color: var(--accent-red); font-size:12px">Error: ${escapeHtml(event.error)}</div>` : ''}
  `;

  dom.modalBackdrop().classList.remove('hidden');
}

window.closeModal = function() {
  dom.modalBackdrop().classList.add('hidden');
};

// Close modal on Escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeModal();
});

// ── Toast ─────────────────────────────────────────────────────────────────────

let toastTimeout;
function showToast(message, type = 'info') {
  const toast = dom.toast();
  toast.textContent = message;
  toast.className = `toast toast--${type} show`;

  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    toast.classList.remove('show');
  }, 4000);
}

// ── Status ────────────────────────────────────────────────────────────────────

function setStatus(type, label) {
  const dot = dom.statusDot();
  const lbl = dom.statusLabel();
  dot.className = `status-dot ${type}`;
  lbl.textContent = label;
}

// ── Clock ─────────────────────────────────────────────────────────────────────

function updateClock() {
  const now = new Date();
  dom.timeDisplay().textContent = now.toLocaleTimeString('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function formatTime(isoString) {
  try {
    const d = new Date(isoString);
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return isoString;
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ── Video File Analysis (File Picker + Upload) ───────────────────────────────

let selectedVideoFile = null;

function onVideoFileSelected(input) {
  const file = input.files[0];
  if (!file) return;
  selectedVideoFile = file;

  const dropContent = document.getElementById("dropZoneContent");
  const dropZone = document.getElementById("videoDropZone");
  const btn = document.getElementById("btnAnalyzeVideo");

  // Show selected file name
  const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
  dropContent.innerHTML = `
    <span class="drop-zone-icon">🎥</span>
    <span class="drop-zone-text drop-zone-filename">${escapeHtml(file.name)}</span>
    <span class="drop-zone-hint">${sizeMB} MB — Click to change</span>
  `;
  dropZone.classList.add("has-file");
  btn.disabled = false;
}

async function triggerVideoUpload() {
  if (!selectedVideoFile) {
    showToast("⚠️ Please select a video file first");
    return;
  }

  const statusEl = document.getElementById("videoAnalysisStatus");
  const btn = document.getElementById("btnAnalyzeVideo");

  btn.disabled = true;
  btn.textContent = "⏳ Uploading & Analyzing...";
  statusEl.className = "video-analysis-status info";
  statusEl.textContent = `Uploading ${selectedVideoFile.name}...`;
  statusEl.classList.remove("hidden");

  try {
    const formData = new FormData();
    formData.append("file", selectedVideoFile);

    const res = await fetch("/api/upload-video", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (res.ok) {
      const isSusp = (data.suspicious || "No").toLowerCase() === "yes";
      statusEl.className = `video-analysis-status ${isSusp ? "alert" : "safe"}`;
      statusEl.textContent = `${isSusp ? "🚨 Suspicious" : "✅ Normal"}: ${data.anomaly_type || "Done"} — ${data.description || ""}`;
      showToast(isSusp ? "🚨 Anomaly Detected!" : "✅ Video Analyzed: Normal");
    } else {
      statusEl.className = "video-analysis-status error";
      statusEl.textContent = `❌ Error: ${data.detail || "Analysis failed"}`;
    }
  } catch (err) {
    statusEl.className = "video-analysis-status error";
    statusEl.textContent = `❌ Failed to connect: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Analyze Video";
  }
}

// Drag & Drop support
document.addEventListener("DOMContentLoaded", () => {
  const zone = document.getElementById("videoDropZone");
  if (!zone) return;

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragleave", () => {
    zone.classList.remove("drag-over");
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("video/")) {
      const input = document.getElementById("videoFileInput");
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      onVideoFileSelected(input);
    } else {
      showToast("⚠️ Please drop a video file (.mp4, .avi, .mkv)");
    }
  });
});

// ── Clear History ─────────────────────────────────────────────────────────

window.clearHistory = async function() {
  if (!confirm('Clear all event history and start fresh?')) return;
  try {
    const res = await fetch('/api/events', { method: 'DELETE' });
    if (res.ok) {
      showToast('🗑️ All events cleared!', 'ok');
    } else {
      showToast('❌ Failed to clear history', 'alert');
    }
  } catch (err) {
    showToast('❌ ' + err.message, 'alert');
  }
};

// ── Init ──────────────────────────────────────────────────────────────────────

// Header buttons: camera publish page (H.264 link) and the private alert topic, from the backend config
async function loadLinks() {
  try {
    const links = await (await fetch('/api/links')).json();
    for (const [id, url] of [['linkCamera', links.camera], ['linkAlerts', links.alerts]]) {
      const el = document.getElementById(id);
      if (el && url) { el.href = url; el.classList.remove('hidden'); }
    }
  } catch (e) {
    console.warn('Could not load header links', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  updateClock();
  setInterval(updateClock, 1000);
  connectWebSocket();
  loadLinks();
});
