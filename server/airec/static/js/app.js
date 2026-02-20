/* AirREC Common Utilities */

// Format seconds to MM:SS
function formatTime(seconds) {
    if (!seconds || seconds < 0) return "00:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// Get status badge HTML
function statusBadge(status) {
    const map = {
        'OK': ['badge-ok', 'OK'],
        'EMPTY': ['badge-empty', 'EMPTY'],
        'MUTED_L': ['badge-muted-l', 'MUTED L'],
        'MUTED_R': ['badge-muted-r', 'MUTED R'],
        'DROPOUT': ['badge-dropout', 'DROPOUT'],
        'MISMATCH': ['badge-mismatch', 'MISMATCH'],
        'START_MISS': ['badge-mismatch', 'START MISS'],
    };
    const [cls, label] = map[status] || ['bg-secondary', status];
    return `<span class="badge ${cls}">${label}</span>`;
}

// Get score circle class
function scoreClass(score) {
    if (score >= 70) return 'score-high';
    if (score >= 40) return 'score-mid';
    return 'score-low';
}

// API helper
async function apiFetch(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
}

// Date helper: today in YYYYMMDD
function todayStr() {
    const d = new Date();
    return d.getFullYear() +
           String(d.getMonth() + 1).padStart(2, '0') +
           String(d.getDate()).padStart(2, '0');
}
