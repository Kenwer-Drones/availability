'use strict';

const $ = id => document.getElementById(id);
const ARIZONA = 'America/Phoenix';
let boardState = { tasks: [], users: {}, user: null };
let editing = null;
let deleting = null;
let busy = false;
let refreshSequence = 0;
let draggedTaskId = null;
const icons = {
    edit: '<path d="m16 3 5 5-12 12-6 1 1-6Z M14 5l5 5"/>',
    trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
    check: '<path d="m5 12 4 4L19 6"/>'
};
function icon(name) {
    return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
}
function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}
function tool(name, label, handler, disabled = false) {
    const button = element('button', 'icon-button');
    button.innerHTML = icon(name);
    button.title = label;
    button.setAttribute('aria-label', label);
    button.disabled = disabled || busy;
    button.onclick = handler;
    return button;
}
function getColors() {
    const palette = [
        ['#e1effe', '#1e429f', '#a4cafe'], ['#fef3c7', '#92400e', '#fcd34d'],
        ['#ede9fe', '#5b21b6', '#c4b5fd'], ['#def7ec', '#03543f', '#84e1bc'],
        ['#fce8f3', '#99154b', '#f8b4d9'], ['#e0f2fe', '#075985', '#7dd3fc'],
        ['#fde8e8', '#9b1c1c', '#f8b4b4'], ['#e5e7eb', '#374151', '#cbd5e1'],
        ['#dcfce7', '#166534', '#86efac'], ['#fae8ff', '#86198f', '#e879f9'],
        ['#ccfbf1', '#115e59', '#5eead4']
    ];
    const colors = {};
    let index = 0;
    Object.keys(boardState.users).sort().forEach(initials => {
        const isChiranjiva = ['CA', 'CRA'].includes(initials) || boardState.users[initials].name.toLowerCase().includes('chiranjiva');
        colors[initials] = isChiranjiva ? ['#ffedd5', '#9a3412', '#fdba74'] : palette[index++ % palette.length];
    });
    return colors;
}
function notice(message = '', retry = false) {
    $('notice').hidden = !message;
    $('notice-text').textContent = message;
    $('retry').hidden = !retry;
}
async function api(path = '', method = 'GET', data) {
    const response = await fetch(`/api/tasks${path}`, {
        method, headers: { 'Content-Type': 'application/json', 'X-Task-Request': '1' },
        body: data === undefined ? undefined : JSON.stringify(data)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status}). Please try again.`);
    return result;
}
async function adminApi(path = '', method = 'GET', data) {
    const response = await fetch(`/api/admin${path}`, {
        method, headers: { 'Content-Type': 'application/json', 'X-Task-Request': '1' },
        body: data === undefined ? undefined : JSON.stringify(data)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status}). Please try again.`);
    return result;
}
async function refresh() {
    const sequence = ++refreshSequence;
    try {
        const state = await api();
        if (sequence !== refreshSequence) return;
        if (!state.user) { openAccount(); return; }
        boardState = state;
        $('board').setAttribute('aria-busy', 'false');
        render();
    } catch (error) {
        if (sequence !== refreshSequence) return;
        if (/401|sign in/i.test(error.message)) { window.location.href = '/auth?next=/tasks'; return; }
        notice(error.message, true);
        $('board').setAttribute('aria-busy', 'false');
        $('board-count').textContent = 'Unable to refresh tasks';
    }
}
function arizonaInput(iso) {
    if (!iso) return '';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
        timeZone: ARIZONA, year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date(iso)).map(p => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}
function deadlineLabel(iso, tz = ARIZONA) {
    return new Intl.DateTimeFormat('en-US', {
        timeZone: tz, month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit', timeZoneName: 'short'
    }).format(new Date(iso));
}
function render() {
    const focusedCard = document.activeElement?.closest('[data-task-id]');
    const focusedLabel = document.activeElement?.getAttribute('aria-label');
    const focusedId = focusedCard?.dataset.taskId;
    const state = boardState;
    $('account-name').textContent = state.user ? (state.users[state.user]?.name || state.user) : '';
    $('account-button').textContent = state.user ? 'Sign out' : 'Sign in';
    $('admin-people').hidden = !state.admin;
    $('add-task').disabled = busy;
    const openCount = state.tasks.filter(t => !t.completed).length;
    $('board-count').textContent = `${openCount} open ${openCount === 1 ? 'task' : 'tasks'} \u00b7 ${Object.keys(state.users).length} team members`;
    const filter = $('search').value.trim().toLowerCase();
    const visible = state.tasks.filter(t => ($('show-completed').checked || !t.completed) && t.title.toLowerCase().includes(filter));
    const colors = getColors();
    const users = Object.keys(state.users).sort((a, b) => a === state.user ? -1 : b === state.user ? 1 : a.localeCompare(b));
    const fragment = document.createDocumentFragment();
    [...users, null].forEach(initials => {
        const column = element('section', 'column');
        const color = colors[initials];
        if (color) ['bg', 'text', 'border'].forEach((part, i) => column.style.setProperty(`--user-${part}`, color[i]));
        const name = initials ? state.users[initials].name || initials : 'Not assigned';
        column.setAttribute('aria-label', name);
        const header = element('div', 'column-heading');
        header.append(element('span', 'avatar', initials || '-'));
        header.append(element('h2', '', name + (initials === state.user ? ' (you)' : '')));
        const tasks = visible.filter(t => {
            if (initials === null) return !t.assignee && !(t.participants || []).length;
            return (t.participants || []).includes(initials) || (!(t.participants || []).length && t.assignee === initials);
        });
        header.append(element('span', 'column-count', String(tasks.length)));
        const add = element('button', 'icon-button', '+');
        add.title = `Add task for ${name}`;
        add.setAttribute('aria-label', add.title);
        add.disabled = busy;
        add.onclick = () => openTask(null, initials);
        header.append(add);
        column.append(header);
        tasks.forEach(task => column.append(taskCard(task)));
        if (!tasks.length) column.append(element('p', 'empty', filter ? 'No matching tasks' : 'No tasks'));
        const addLink = element('button', 'add-column', '+ Add task');
        addLink.disabled = busy;
        addLink.onclick = () => openTask(null, initials);
        column.append(addLink);
        fragment.append(column);
    });
    $('board').replaceChildren(fragment);
    if (state.admin && location.hash === '#people' && !$('people-dialog').open) {
        history.replaceState(null, '', '/tasks');
        renderPeople();
        $('people-dialog').showModal();
    }
    if (focusedId && focusedLabel) {
        const card = Array.from($('board').querySelectorAll('[data-task-id]')).find(el => el.dataset.taskId === focusedId);
        const control = Array.from(card?.querySelectorAll('[aria-label]') || []).find(el => el.getAttribute('aria-label') === focusedLabel);
        if (control && !control.disabled) control.focus({ preventScroll: true });
    }
}
function taskCard(task) {
    const card = element('article', `task-card${task.completed ? ' completed' : ''}`);
    card.dataset.taskId = task.id;
    card.draggable = !task.completed && !!boardState.user;
    if (card.draggable) {
        card.title = 'Drag to reorder within this deadline';
        card.addEventListener('dragstart', event => {
            if (busy) { event.preventDefault(); return; }
            card.classList.add('dragging');
            draggedTaskId = task.id;
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', task.id);
        });
        card.addEventListener('dragend', () => {
            card.classList.remove('dragging');
            draggedTaskId = null;
            document.querySelectorAll('.drop-before, .drop-after, .drop-invalid').forEach(el => el.classList.remove('drop-before', 'drop-after', 'drop-invalid'));
        });
        card.addEventListener('dragover', event => {
            event.preventDefault();
            const sourceId = draggedTaskId || event.dataTransfer.getData('text/plain');
            const source = boardState.tasks.find(item => item.id === sourceId);
            const valid = source && source.id !== task.id && source.assignee === task.assignee && source.deadline === task.deadline;
            card.classList.toggle('drop-invalid', !valid);
            card.classList.toggle('drop-before', valid && event.offsetY < card.offsetHeight / 2);
            card.classList.toggle('drop-after', valid && event.offsetY >= card.offsetHeight / 2);
            event.dataTransfer.dropEffect = valid ? 'move' : 'none';
        });
        card.addEventListener('dragleave', () => card.classList.remove('drop-before', 'drop-after', 'drop-invalid'));
        card.addEventListener('drop', event => {
            event.preventDefault();
            const sourceId = draggedTaskId || event.dataTransfer.getData('text/plain');
            const source = boardState.tasks.find(item => item.id === sourceId);
            if (!source || source.id === task.id || source.assignee !== task.assignee || source.deadline !== task.deadline) {
                notice('Tasks can be reordered only within the same assignee and deadline.');
                return;
            }
            mutate(`/${source.id}/move`, 'POST', {
                version: source.version,
                target_id: task.id,
                after: event.offsetY >= card.offsetHeight / 2
            });
        });
    }
    const top = element('div', 'task-top');
    const checkbox = element('input', 'completion');
    checkbox.type = 'checkbox';
    checkbox.checked = !!task.completed;
    const isParticipant = (task.participants || []).includes(boardState.user) || (!(task.participants || []).length && task.assignee === boardState.user);
    checkbox.disabled = busy || !boardState.user || !isParticipant;
    checkbox.title = isParticipant ? (task.completed ? 'Reopen shared task' : 'Complete shared task') : 'Only a task participant can complete this task';
    checkbox.setAttribute('aria-label', `${checkbox.title}: ${task.title}`);
    checkbox.onchange = () => mutate(`/${task.id}/completion`, 'POST', { version: task.version, completed: checkbox.checked });
    top.append(checkbox, element('h3', 'task-title', task.title));
    card.append(top);
    const meta = element('div', 'task-meta');
    const deadline = element('div', `deadline${task.deadline && !task.completed && new Date(task.deadline) < new Date() ? ' overdue' : ''}`);
    deadline.innerHTML = icon('calendar');
    deadline.append(element('span', '', task.deadline ? deadlineLabel(task.deadline) : 'No deadline'));
    if (task.deadline) {
        const localTz = boardState.users[boardState.user]?.timezone || ARIZONA;
        try { deadline.title = deadlineLabel(task.deadline, localTz); } catch (_) { /* Invalid legacy timezone: keep Arizona label. */ }
    }
    meta.append(deadline);
    const otherParticipants = (task.participants || []).filter(initials => initials !== task.assignee).map(initials => boardState.users[initials]?.name || initials);
    if (otherParticipants.length) meta.append(element('div', 'shared-with', `Also assigned to: ${otherParticipants.join(', ')}`));
    card.append(meta);
    const footer = element('div', 'task-footer');
    const order = element('div', 'task-tools');
    if (!task.completed) order.append(element('span', 'drag-hint', 'Drag to reorder'));
    const actions = element('div', 'task-tools');
    if (!task.completed) actions.append(tool('edit', 'Edit task', () => openTask(task), !boardState.user));
    if (boardState.user && [task.assignee, task.created_by].includes(boardState.user)) actions.append(tool('trash', 'Delete task for everyone', () => {
        deleting = task;
        $('delete-name').textContent = task.title;
        $('delete-error').textContent = '';
        $('delete-dialog').showModal();
    }));
    else if (boardState.user && (task.participants || []).includes(boardState.user)) actions.append(tool('trash', 'Remove task from my list', () => removeFromMyList(task)));
    footer.append(order, actions);
    card.append(footer);
    return card;
}
async function removeFromMyList(task) {
    if (!confirm('Remove this shared task from your list? It will remain visible to the other participants.')) return;
    await mutate(`/${task.id}/membership`, 'DELETE', { version: task.version });
}
async function mutate(path, method, data) {
    if (busy) return;
    busy = true;
    $('board').querySelectorAll('button, input').forEach(control => { control.disabled = true; });
    $('add-task').disabled = true;
    try { await api(path, method, data); notice(); }
    catch (error) { notice(error.message); }
    finally { busy = false; await refresh(); }
}
function openAccount() { location.assign('/auth?next=/tasks'); }
function openTask(task = null, assignee = boardState.user) {
    if (!boardState.user) { openAccount(); return; }
    editing = task;
    $('task-form').reset();
    $('task-dialog-title').textContent = task ? 'Edit task' : 'Add task';
    $('task-submit').textContent = task ? 'Save changes' : 'Add task';
    $('task-title').value = task?.title || '';
    $('task-assignee').replaceChildren(new Option('Not assigned', ''));
    Object.keys(boardState.users).sort().forEach(initials => {
        $('task-assignee').add(new Option(`${boardState.users[initials].name || initials} (${initials})`, initials));
    });
    $('task-assignee').value = (task ? task.assignee : assignee) || '';
    $('task-participants').replaceChildren();
    const selectedParticipants = new Set(task?.participants || []);
    Object.keys(boardState.users).sort().forEach(initials => {
        const option = new Option(`${boardState.users[initials].name || initials} (${initials})`, initials);
        option.selected = selectedParticipants.has(initials);
        $('task-participants').add(option);
    });
    $('task-assignee').disabled = !!task?.assignee && task.assignee !== boardState.user;
    $('assignee-note').hidden = !$('task-assignee').disabled;
    $('task-deadline').value = arizonaInput(task?.deadline);
    $('task-error').textContent = '';
    previewDeadline();
    $('task-dialog').showModal();
}
function previewDeadline() {
    const value = $('task-deadline').value;
    const tz = boardState.users[boardState.user]?.timezone || ARIZONA;
    $('deadline-preview').textContent = '';
    if (value && tz !== ARIZONA) {
        try { $('deadline-preview').textContent = `Your timezone: ${deadlineLabel(`${value}:00-07:00`, tz)}`; } catch (_) { /* Wait for a valid date input. */ }
    }
}
function submitForm(formId, errorId, action) {
    $(formId).addEventListener('submit', async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const submit = form.querySelector('button.primary, button.danger');
        if (submit.disabled) return;
        submit.disabled = true;
        $(errorId).textContent = '';
        try { await action(); notice(); }
        catch (error) { $(errorId).textContent = error.message; await refresh(); }
        finally { submit.disabled = false; }
    });
}
submitForm('task-form', 'task-error', async () => {
    await api(editing ? `/${editing.id}` : '', editing ? 'PATCH' : 'POST', {
        title: $('task-title').value, assignee: $('task-assignee').value || null,
        participants: Array.from($('task-participants').selectedOptions).map(option => option.value),
        deadline: $('task-deadline').value || null, ...(editing ? { version: editing.version } : {})
    });
    $('task-dialog').close();
    await refresh();
});
submitForm('delete-form', 'delete-error', async () => {
    await api(`/${deleting.id}`, 'DELETE', { version: deleting.version });
    $('delete-dialog').close();
    await refresh();
});
function renderPeople() {
    const list = $('people-list');
    list.replaceChildren();
    $('people-error').textContent = '';
    Object.keys(boardState.users).sort().forEach(initials => {
        const row = element('div', 'person-row');
        row.append(element('strong', '', `${boardState.users[initials].name || initials} (${initials})`));
        if (initials === 'CR') row.append(element('span', 'muted', 'Administrator'));
        else {
            const remove = element('button', 'danger', 'Delete');
            remove.onclick = async () => {
                if (!confirm(`Delete ${boardState.users[initials].name || initials} from the team?`)) return;
                try { await adminApi(`/users/${initials}`, 'DELETE', {}); $('people-dialog').close(); await refresh(); }
                catch (error) { $('people-error').textContent = error.message; }
            };
            row.append(remove);
        }
        list.append(row);
    });
}
$('account-button').onclick = async () => {
    if (!boardState.user) { window.location.href = '/auth?next=/tasks'; return; }
    try { await api('/logout', 'POST', {}); await refresh(); } catch (error) { notice(error.message); }
};
$('add-task').onclick = () => openTask();
$('admin-people').onclick = () => { renderPeople(); $('people-dialog').showModal(); };
$('search').oninput = render;
$('show-completed').onchange = render;
$('task-deadline').oninput = previewDeadline;
$('retry').onclick = () => { notice(); refresh(); };
document.querySelectorAll('.close-dialog').forEach(button => button.onclick = () => button.closest('dialog').close());
if (typeof io === 'function') {
    const socket = io();
    socket.on('connect', () => { $('connection').textContent = 'Live'; refresh(); });
    socket.on('disconnect', () => { $('connection').textContent = 'Reconnecting...'; });
    socket.on('connect_error', () => { $('connection').textContent = 'Periodic refresh'; });
    socket.on('task_update', refresh);
    socket.on('tz_update', refresh);
} else { $('connection').textContent = 'Periodic refresh'; }
setInterval(() => { if (!document.hidden && !busy && !document.querySelector('dialog[open]')) refresh(); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
refresh();
