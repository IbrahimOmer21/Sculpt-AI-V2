'use strict';
// Sculpt AI front end. One page; the URL hash (#/g/<id>) says which generation is open.

const $ = id => document.getElementById(id);
const MAX_IMAGES = 3;
const MAX_UPLOAD = 10 * 1024 * 1024;
const LONG_WAIT = 'This usually takes 2–4 minutes. Keep this tab open.';

const state = {
    gen: null,      // the open generation (API shape), or null on the home page
    gens: [],       // sidebar list, newest first
    recent: [],     // recent first prompts for suggestions
    busy: false,
    pending: null,  // {prompt} or {imageUrl} shown while the server works
};

// ── Small helpers ──
// Builds elements with textContent, never innerHTML, so names and prompts can't inject HTML.
function h(tag, props = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
        else if (value === true) el.setAttribute(key, '');
        else if (value !== false && value != null) el.setAttribute(key, value);
    }
    el.append(...children.flat().filter(c => c != null && c !== false));
    return el;
}

function icon(name) {
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(NS, 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.append(use);
    return svg;
}

async function api(path, { method = 'GET', json, form } = {}) {
    const options = { method, headers: {} };
    if (form) options.body = form;
    else if (method !== 'GET') {
        // The server only accepts JSON on API writes (CSRF protection), even when there's no body.
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(json || {});
    }
    let response;
    try {
        response = await fetch(path, options);
    } catch {
        throw new Error('Could not reach the server. Check your connection and try again.');
    }
    if (response.status === 401) {
        location.href = '/login';
        throw new Error('Please log in again.');
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
    return data;
}

let toastTimer;
function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.className = 'toast' + (isError ? ' error' : '');
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
}

function bell() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const now = ctx.currentTime;
        [880, 1320, 1760].forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.18 / (i + 1), now);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + 1.6);
            osc.connect(gain).connect(ctx.destination);
            osc.start(now);
            osc.stop(now + 1.6);
        });
        setTimeout(() => ctx.close(), 1800);
    } catch { /* sound is a nice-to-have */ }
}

let busyTimer;
function setBusy(text) {
    state.busy = Boolean(text);
    $('status').hidden = !text;
    $('status-text').textContent = text || '';
    clearInterval(busyTimer);
    if (text) {
        const start = Date.now();
        const tick = () => {
            const s = Math.floor((Date.now() - start) / 1000);
            $('status-time').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
        };
        tick();
        busyTimer = setInterval(tick, 1000);
    }
    render();
}

const scrollToEnd = () => requestAnimationFrame(() =>
    ($('sculpture') || $('thread').lastElementChild)?.scrollIntoView({ behavior: 'smooth', block: 'end' }));
const imageCount = () => state.gen ? state.gen.iterations.length : 0;
const isSculpted = () => Boolean(state.gen && (state.gen.sculpture_url || state.gen.in_progress));
// The in-flight prompt/upload belongs to one generation; don't draw it in another one the user switched to.
const pendingHere = () => Boolean(state.pending) && state.pending.genId === (state.gen?.generation_id ?? null);
const isEmpty = () => !pendingHere() && (!state.gen || (!state.gen.iterations.length && !state.gen.source_image_url));

// ── Rendering ──
function render() {
    $('home').hidden = !isEmpty();
    $('thread').hidden = isEmpty();
    if (isEmpty()) renderHome(); else renderThread();
    renderList();
    renderComposer();
    const title = state.gen?.name || 'Sculpt AI';
    $('topbar-title').textContent = title;
    document.title = state.gen?.name ? `${title} · Sculpt AI` : 'Sculpt AI';
}

function renderList() {
    const activeId = state.gen?.generation_id;
    if (!state.gens.length) {
        $('gen-list').replaceChildren(h('p', { class: 'gen-empty' }, 'No sculptures yet. Your creations will appear here.'));
        return;
    }
    $('gen-list').replaceChildren(...state.gens.map(g => {
        const name = g.name || 'Untitled';
        return h('div', { class: 'gen-item' + (g.generation_id === activeId ? ' active' : '') },
            h('a', { class: 'gen-link', href: `#/g/${g.generation_id}`, title: name,
                     'aria-current': g.generation_id === activeId ? 'page' : false }, name),
            h('button', { class: 'icon-btn', type: 'button', 'aria-pressed': String(g.is_favorite),
                          'aria-label': `Favourite "${name}"`, title: 'Favourite', onclick: () => toggleFavorite(g) }, icon('star')),
            h('button', { class: 'icon-btn delete-btn', type: 'button', 'aria-label': `Delete "${name}"`,
                          title: 'Delete', onclick: () => deleteGeneration(g) }, icon('trash')),
        );
    }));
}

function renderHome() {
    $('suggestions').hidden = !state.recent.length;
    $('chips').replaceChildren(...state.recent.map(prompt =>
        h('button', { class: 'chip', type: 'button', title: prompt, disabled: state.busy,
                      onclick: () => { $('prompt').value = prompt; sendPrompt(prompt); } }, prompt)));

    const sculptures = state.gens.filter(g => g.has_sculpture && g.thumbnail_url);
    $('history').hidden = !sculptures.length;
    $('history-row').replaceChildren(...sculptures.map(g =>
        h('a', { class: 'history-card', href: `#/g/${g.generation_id}` },
            h('img', { src: g.thumbnail_url, alt: '', loading: 'lazy', width: 148, height: 148 }),
            h('span', {}, g.name || 'Untitled'))));
    $('drop-zone').disabled = state.busy;
}

function renderThread() {
    const g = state.gen || { iterations: [] };  // null while the very first request is still running
    const canSculpt = !isSculpted() && !state.busy;
    const turns = g.iterations.map(it => h('div', { class: 'turn' },
        h('p', { class: 'bubble' }, it.prompt),
        h('div', { class: 'result' + (g.source_image_url === it.image_url ? ' chosen' : '') },
            h('img', { src: it.image_url, alt: `Image ${it.iteration_order}: ${it.prompt}`, width: 440, height: 440 }),
            h('div', { class: 'result-bar' },
                h('span', { class: 'meta' }, `Image ${it.iteration_order} of ${MAX_IMAGES}`),
                h('a', { class: 'btn btn-sm', href: it.image_url, download: `${g.name || 'sculpt'}-${it.iteration_order}.png` },
                  icon('download'), 'PNG'),
                canSculpt && h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: () => sculpt(it) },
                  icon('box'), 'Sculpt this')))));

    const uploaded = !g.iterations.length && g.source_image_url;
    const pending = pendingHere() ? state.pending : null;
    $('thread').replaceChildren(...[
        h('h1', { class: 'thread-title' }, g.name || 'New sculpture'),
        uploaded && h('div', { class: 'turn' },
            h('p', { class: 'bubble' }, 'Your uploaded image'),
            h('div', { class: 'result chosen' }, h('img', { src: g.source_image_url, alt: 'Your uploaded image', width: 440, height: 440 }))),
        ...turns,
        pending?.prompt && h('div', { class: 'turn' }, h('p', { class: 'bubble' }, pending.prompt)),
        pending?.imageUrl && h('div', { class: 'turn' },
            h('p', { class: 'bubble' }, 'Your uploaded image'),
            h('div', { class: 'result' }, h('img', { src: pending.imageUrl, alt: 'Your uploaded image' }))),
        g.sculpture_url && h('div', { class: 'sculpture', id: 'sculpture' },
            h('model-viewer', { src: g.sculpture_url, alt: `3D model of ${g.name || 'your sculpture'}`,
                                'camera-controls': true, 'auto-rotate': true, 'shadow-intensity': '1', 'touch-action': 'pan-y' }),
            h('div', { class: 'sculpture-bar' },
                h('span', { class: 'meta' }, 'Your sculpture is ready'),
                h('a', { class: 'btn btn-sm', href: g.sculpture_url, download: `${g.name || 'sculpture'}.glb` }, icon('download'), 'GLB · view'),
                h('a', { class: 'btn btn-sm btn-primary', href: g.stl_url, download: `${g.name || 'sculpture'}.stl` }, icon('download'), 'STL · 3D print'))),
        g.in_progress && !state.busy && h('p', { class: 'notice' }, 'This sculpture is still being made. Refresh the page in a minute to see it.'),
    ].filter(Boolean));
}

function renderComposer() {
    const count = imageCount();
    const canPrompt = !state.busy && !isSculpted() && count < MAX_IMAGES;
    $('prompt').disabled = !canPrompt;
    $('send-btn').disabled = !canPrompt;
    $('attach-btn').hidden = count > 0 || isSculpted();
    $('attach-btn').disabled = state.busy;
    $('prompt').placeholder = count ? 'Describe a change, e.g. make it bronze' : 'Describe your sculpture…';
    $('send-btn').setAttribute('aria-label', count ? 'Refine image' : 'Generate image');

    let hint = 'Enter to send · Shift+Enter for a new line';
    let important = false;
    if (state.gen?.in_progress) { hint = 'Your sculpture is still being made. Refresh in a minute to see it.'; important = true; }
    else if (isSculpted()) { hint = 'This sculpture is finished. Start a new one with "New sculpture".'; important = true; }
    else if (count >= MAX_IMAGES) { hint = 'All 3 images used. Pick your favourite above and press "Sculpt this".'; important = true; }
    else if (count) { hint = `${count} of ${MAX_IMAGES} images used · refine it, or press "Sculpt this" on an image`; important = true; }
    $('composer-hint').textContent = hint;
    $('composer-hint').classList.toggle('important', important);
}

// ── Data loading + navigation ──
async function refreshList() {
    try {
        [state.gens, state.recent] = await Promise.all([api('/api/generations'), api('/api/prompts/recent')]);
    } catch (e) {
        toast(e.message, true);
    }
    render();
}

async function route() {
    closeMenu();
    const match = location.hash.match(/^#\/g\/(\d+)$/);
    if (!match) {
        state.gen = null;
        render();
        return;
    }
    try {
        state.gen = await api(`/api/generation/${match[1]}`);
        render();
        $('scroll').scrollTop = 0;
    } catch (e) {
        toast(e.message, true);
        location.hash = '#/';
    }
}

async function ensureGeneration() {
    if (state.gen) return state.gen.generation_id;
    const { generation_id } = await api('/api/generation', { method: 'POST' });
    state.gen = { generation_id, name: null, iterations: [], source_image_url: null, sculpture_url: null, in_progress: false };
    history.pushState(null, '', `#/g/${generation_id}`);  // pushState doesn't fire hashchange, so no reload
    return generation_id;
}

// ── Actions ──
async function sendPrompt(text) {
    const prompt = text.trim();
    if (state.busy) return;
    if (!prompt) {
        toast('Please type a prompt first.', true);
        $('prompt').focus();
        return;
    }
    const first = imageCount() === 0;
    state.pending = { prompt, genId: state.gen?.generation_id ?? null };
    setBusy(first ? 'Creating your image…' : 'Refining your image…');
    scrollToEnd();
    try {
        const id = await ensureGeneration();
        state.pending.genId = id;
        const gen = state.gen;  // the user may open another generation while this one is working
        const iteration = await api(`/api/generation/${id}/iterate`, { method: 'POST', json: { prompt } });
        gen.iterations.push(iteration);
        gen.name = iteration.name;
        $('prompt').value = '';
        autosize();
        bell();
        if (first) refreshList();
    } catch (e) {
        toast(e.message, true);
    } finally {
        state.pending = null;
        setBusy(null);
        scrollToEnd();
        if (!$('prompt').disabled && !matchMedia('(hover: none)').matches) $('prompt').focus();
    }
}

async function sculpt(iteration) {
    if (state.busy) return;
    const id = state.gen.generation_id;
    setBusy(`Sculpting your 3D model… ${LONG_WAIT}`);
    try {
        const result = await api(`/api/generation/${id}/sculpture`, { method: 'POST', json: { iteration_id: iteration.iteration_id } });
        if (state.gen?.generation_id === id) state.gen = result;
        bell();
        refreshList();
    } catch (e) {
        toast(e.message, true);
    } finally {
        setBusy(null);
        scrollToEnd();
    }
}

async function uploadImage(file) {
    if (!file || state.busy) return;
    $('file-input').value = '';  // lets the same file be picked again after an error
    if (!/\.(png|jpe?g)$/i.test(file.name)) return toast('Only PNG and JPG images are allowed.', true);
    if (file.size > MAX_UPLOAD) return toast('That image is too big (max 10 MB).', true);

    state.pending = { imageUrl: URL.createObjectURL(file), genId: state.gen?.generation_id ?? null };
    setBusy(`Sculpting your image… ${LONG_WAIT}`);
    try {
        const id = await ensureGeneration();
        state.pending.genId = id;
        const form = new FormData();
        form.append('image', file);
        const result = await api(`/api/generation/${id}/upload`, { method: 'POST', form });
        if (state.gen?.generation_id === id) state.gen = result;
        bell();
        refreshList();
    } catch (e) {
        toast(e.message, true);
    } finally {
        URL.revokeObjectURL(state.pending.imageUrl);
        state.pending = null;
        setBusy(null);
        scrollToEnd();
    }
}

async function toggleFavorite(g) {
    try {
        g.is_favorite = (await api(`/api/generation/${g.generation_id}/favorite`, { method: 'PATCH' })).is_favorite;
        renderList();
    } catch (e) {
        toast(e.message, true);
    }
}

async function deleteGeneration(g) {
    if (!confirm(`Delete "${g.name || 'Untitled'}"?\n\nIts images and 3D files are deleted too. This can't be undone.`)) return;
    try {
        await api(`/api/generation/${g.generation_id}`, { method: 'DELETE' });
        state.gens = state.gens.filter(x => x.generation_id !== g.generation_id);
        toast('Sculpture deleted.');
        if (state.gen?.generation_id === g.generation_id) location.hash = '#/';
        refreshList();
    } catch (e) {
        toast(e.message, true);
    }
}

// ── Mobile menu ──
function openMenu() {
    $('sidebar').classList.add('open');
    $('scrim').hidden = false;
    $('open-menu').setAttribute('aria-expanded', 'true');
    $('close-menu').focus();
}
function closeMenu() {
    if (!$('sidebar').classList.contains('open')) return;
    $('sidebar').classList.remove('open');
    $('scrim').hidden = true;
    $('open-menu').setAttribute('aria-expanded', 'false');
    $('open-menu').focus();
}

// ── Theme ──
function currentTheme() {
    return document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}
function showThemeIcon() {
    const dark = currentTheme() === 'dark';
    $('theme-btn').replaceChildren(icon(dark ? 'sun' : 'moon'));
    $('theme-btn').setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
}

// ── Composer ──
function autosize() {
    const el = $('prompt');
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

// ── Wiring ──
$('composer').addEventListener('submit', e => { e.preventDefault(); sendPrompt($('prompt').value); });
$('prompt').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendPrompt($('prompt').value); }
});
$('prompt').addEventListener('input', autosize);

$('attach-btn').addEventListener('click', () => $('file-input').click());
$('drop-zone').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', e => uploadImage(e.target.files[0]));
$('drop-zone').addEventListener('dragover', e => { e.preventDefault(); $('drop-zone').classList.add('drag-over'); });
$('drop-zone').addEventListener('dragleave', () => $('drop-zone').classList.remove('drag-over'));
$('drop-zone').addEventListener('drop', e => {
    e.preventDefault();
    $('drop-zone').classList.remove('drag-over');
    uploadImage(e.dataTransfer.files[0]);
});
// A file dropped anywhere else would make the browser navigate away to open it.
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());

$('open-menu').addEventListener('click', openMenu);
$('close-menu').addEventListener('click', closeMenu);
$('scrim').addEventListener('click', closeMenu);
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });

$('theme-btn').addEventListener('click', () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch { /* private mode */ }
    showThemeIcon();
});

window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', e => { if (state.busy) e.preventDefault(); });

showThemeIcon();
refreshList().then(route);
