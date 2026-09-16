'use strict';

const $ = id => document.getElementById(id);
const ARIZONA = 'America/Phoenix';
let boardState = { tasks: [], users: {}, user: null };
let editing = null;
let deleting = null;
let busy = false;
let refreshSequence = 0;
let draggedTaskId = null;
let dependencyDrag = null;
let cutMode = false;
const icons = {
    edit: '<path d="m16 3 5 5-12 12-6 1 1-6Z M14 5l5 5"/>',
    trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
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
function commentTime(iso) {
    return new Intl.DateTimeFormat('en-US', {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
    }).format(new Date(iso));
}
function arizonaDateParts(iso) {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: ARIZONA, year: 'numeric', month: 'numeric', day: 'numeric'
    }).formatToParts(new Date(iso));
    return Object.fromEntries(parts.filter(part => part.type !== 'literal')
        .map(part => [part.type, Number(part.value)]));
}
function dateAgeInDays(iso) {
    if (!iso) return 0;
    const today = arizonaDateParts(new Date().toISOString());
    const deadline = arizonaDateParts(iso);
    const todayUtc = Date.UTC(today.year, today.month - 1, today.day);
    const deadlineUtc = Date.UTC(deadline.year, deadline.month - 1, deadline.day);
    return Math.round((todayUtc - deadlineUtc) / 86400000);
}
function shadeHex(hex, steps) {
    const value = hex.replace('#', '');
    const channels = [0, 2, 4].map(index => parseInt(value.slice(index, index + 2), 16));
    const amount = Math.max(-120, Math.min(120, steps * 18));
    const adjusted = channels.map(channel => {
        // Past dates darken; future dates lighten. Each day is one clear step.
        if (amount >= 0) return Math.max(0, channel - amount);
        return Math.min(255, channel + Math.round((255 - channel) * (-amount / 255)));
    });
    return '#' + adjusted.map(channel => channel.toString(16).padStart(2, '0')).join('');
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
    const defaultUsers = Object.keys(state.users).sort((a, b) => a === state.user ? -1 : b === state.user ? 1 : a.localeCompare(b));
    const users = [...(state.column_order || []).filter(value => state.users[value]), ...defaultUsers.filter(value => !(state.column_order || []).includes(value))];
    const fragment = document.createDocumentFragment();
    [...users, null].forEach(initials => {
        const column = element('section', 'column');
        const color = colors[initials];
        if (color) ['bg', 'text', 'border'].forEach((part, i) => column.style.setProperty(`--user-${part}`, color[i]));
        const name = initials ? state.users[initials].name || initials : 'Not assigned';
        column.setAttribute('aria-label', name);
        const header = element('div', 'column-heading');
        if (initials) {
            header.draggable = true;
            header.dataset.columnId = initials;
            header.addEventListener('dragstart', event => { event.dataTransfer.setData('text/column', initials); header.classList.add('column-dragging'); });
            header.addEventListener('dragend', () => header.classList.remove('column-dragging'));
            header.addEventListener('dragover', event => { event.preventDefault(); header.classList.add('column-drop-target'); });
            header.addEventListener('dragleave', () => header.classList.remove('column-drop-target'));
            header.addEventListener('drop', async event => { event.preventDefault(); header.classList.remove('column-drop-target'); const moved = event.dataTransfer.getData('text/column'); if (!moved || moved === initials) return; const order = [...users]; const from = order.indexOf(moved); const to = order.indexOf(initials); order.splice(from, 1); order.splice(to, 0, moved); try { await api('/layout', 'POST', { order }); await refresh(); } catch (error) { notice(error.message); } });
        }
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
    requestAnimationFrame(drawDependencyLines);
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
    const assigneeColor = boardState.users[task.assignee] ? getColors()[task.assignee] : null;
    const age = dateAgeInDays(task.deadline);
    if (assigneeColor) {
        // Keep each user's base color, then shift it one shade per day from today.
        // Positive age means the deadline is in the past, so it becomes darker.
        card.style.setProperty('--task-bg', shadeHex(assigneeColor[0], age));
        card.style.setProperty('--task-border', shadeHex(assigneeColor[2], age));
    }
    card.dataset.taskId = task.id;
    card.append(dependencyHandle(task));
    card.draggable = !task.completed && !!boardState.user;
    if (card.draggable) {
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
    const deadlineText = element('span', '', task.deadline ? deadlineLabel(task.deadline) : 'No deadline');
    if (task.deadline && assigneeColor) {
        // The date itself follows the same relative shade as its task card.
        deadlineText.style.color = shadeHex(assigneeColor[1], age);
        deadlineText.title = age > 0 ? `${age} day${age === 1 ? '' : 's'} past today` :
            age < 0 ? `${Math.abs(age)} day${age === -1 ? '' : 's'} ahead of today` : 'Today';
    }
    deadline.append(deadlineText);
    if (task.deadline) {
        const localTz = boardState.users[boardState.user]?.timezone || ARIZONA;
        try { deadline.title = deadlineLabel(task.deadline, localTz); } catch (_) { /* Invalid legacy timezone: keep Arizona label. */ }
    }
    meta.append(deadline);
    const otherParticipants = (task.participants || []).filter(initials => initials !== task.assignee).map(initials => boardState.users[initials]?.name || initials);
    if (otherParticipants.length) meta.append(element('div', 'shared-with', `Also assigned to: ${otherParticipants.join(', ')}`));
    card.append(meta);
    const footer = element('div', 'task-footer');
    const actions = element('div', 'task-tools');
    if (!task.completed) actions.append(tool('edit', 'Edit task', () => openTask(task), !boardState.user));
    if (boardState.user && [task.assignee, task.created_by].includes(boardState.user)) actions.append(tool('trash', 'Delete task for everyone', () => {
        deleting = task;
        $('delete-name').textContent = task.title;
        $('delete-error').textContent = '';
        $('delete-dialog').showModal();
    }));
    else if (boardState.user && (task.participants || []).includes(boardState.user)) actions.append(tool('trash', 'Remove task from my list', () => removeFromMyList(task)));
    footer.append(actions);
    card.append(footer);
    return card;
}
function dependencyHandle(task) {
    const group = element('span', 'dependency-handles');
    ['top', 'right', 'bottom', 'left'].forEach(side => {
        const handle = element('button', `dependency-handle dependency-handle-${side}`, '·');
        handle.type = 'button';
        handle.dataset.side = side;
        handle.title = `Drag from the ${side} side to connect a task this task depends on`;
        handle.setAttribute('aria-label', `Connect a task this task depends on from the ${side} side`);
        handle.addEventListener('pointerdown', event => {
            event.preventDefault();
            event.stopPropagation();
            dependencyDrag = { taskId: task.id, side, x: event.clientX, y: event.clientY };
            handle.setPointerCapture?.(event.pointerId);
            document.body.classList.add('drawing-dependency');
            drawDependencyLines();
        });
        handle.addEventListener('pointermove', event => {
            if (!dependencyDrag) return;
            dependencyDrag.x = event.clientX;
            dependencyDrag.y = event.clientY;
            drawDependencyLines();
        });
        handle.addEventListener('pointerup', async event => {
            if (!dependencyDrag) return;
            const sourceId = dependencyDrag.taskId;
            const sourceSide = dependencyDrag.side;
            dependencyDrag = null;
            document.body.classList.remove('drawing-dependency');
            const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-task-id]');
            if (!target || target.dataset.taskId === sourceId) { drawDependencyLines(); return; }
            const source = boardState.tasks.find(item => item.id === sourceId);
            try {
                const targetRect = target.getBoundingClientRect();
                const targetSide = nearestSide(targetRect, event.clientX, event.clientY);
                await api('/' + sourceId + '/dependencies', 'POST', { version: source.version, prerequisite_id: target.dataset.taskId, source_side: sourceSide, target_side: targetSide });
                await refresh();
            } catch (error) { notice(error.message); drawDependencyLines(); }
        });
        group.append(handle);
    });
    return group;
}
function sidePoint(rect, side, boardRect, scrollLeft, scrollTop) {
    const x = rect.left - boardRect.left + scrollLeft;
    const y = rect.top - boardRect.top + scrollTop;
    if (side === 'top') return { x: x + rect.width / 2, y };
    if (side === 'right') return { x: x + rect.width, y: y + rect.height / 2 };
    if (side === 'bottom') return { x: x + rect.width / 2, y: y + rect.height };
    return { x, y: y + rect.height / 2 };
}
function nearestSide(rect, clientX, clientY) {
    const distances = {
        top: Math.abs(clientY - rect.top),
        right: Math.abs(clientX - rect.right),
        bottom: Math.abs(clientY - rect.bottom),
        left: Math.abs(clientX - rect.left)
    };
    return Object.entries(distances).sort((a, b) => a[1] - b[1])[0][0];
}
function drawDependencyLines() {
    const board = $('board');
    if (!board) return;
    let svg = board.querySelector('.dependency-layer');
    if (!svg) {
        svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.classList.add('dependency-layer');
        board.prepend(svg);
    }
    svg.replaceChildren();
    const boardRect = board.getBoundingClientRect();
    svg.setAttribute('width', board.scrollWidth);
    svg.setAttribute('height', board.scrollHeight);
    svg.setAttribute('viewBox', `0 0 ${board.scrollWidth} ${board.scrollHeight}`);
    const taskMap = new Map(Array.from(board.querySelectorAll('[data-task-id]')).map(card => [card.dataset.taskId, card]));
    boardState.tasks.forEach(task => (task.dependencies || []).forEach(dependency => {
        const prerequisiteId = typeof dependency === 'string' ? dependency : dependency.task_id;
        const from = taskMap.get(task.id);
        const to = taskMap.get(prerequisiteId);
        if (!from || !to) return;
        const a = from.getBoundingClientRect();
        const b = to.getBoundingClientRect();
        const start = sidePoint(a, typeof dependency === 'string' ? 'right' : dependency.source_side || 'right', boardRect, board.scrollLeft, board.scrollTop);
        const targetSide = typeof dependency === 'string' ? 'left' : dependency.target_side || 'left';
        const end = sidePoint(b, targetSide, boardRect, board.scrollLeft, board.scrollTop);
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        const bend = Math.max(18, Math.abs(end.x - start.x) / 2);
        line.setAttribute('d', `M ${start.x} ${start.y} C ${start.x + bend} ${start.y}, ${end.x - bend} ${end.y}, ${end.x} ${end.y}`);
        line.classList.add('dependency-line');
        line.setAttribute('tabindex', '0');
        line.setAttribute('aria-label', 'Cut task dependency');
        line.title = 'Click this dotted connection to cut it';
        const cutConnection = async () => {
            if (!confirm('Remove this dependency? This task will no longer wait for the task it depends on.')) return;
            try { await api('/' + task.id + '/dependencies/' + prerequisiteId, 'DELETE', { version: task.version }); cutMode = false; document.body.classList.remove('cut-mode'); $('cut-dependency').setAttribute('aria-pressed', 'false'); await refresh(); }
            catch (error) { notice(error.message); }
        };
        line.addEventListener('click', cutConnection);
        line.addEventListener('mouseenter', () => { line.classList.add('dependency-line-hover'); notice('Click the dotted line to cut this connection.'); });
        line.addEventListener('mouseleave', () => line.classList.remove('dependency-line-hover'));
        const hit = line.cloneNode();
        hit.classList.remove('dependency-line');
        hit.classList.add('dependency-line-hit');
        hit.removeAttribute('tabindex');
        hit.setAttribute('aria-hidden', 'true');
        hit.addEventListener('click', cutConnection);
        svg.append(hit, line);
    }));
    if (dependencyDrag) {
        const from = taskMap.get(dependencyDrag.taskId);
        if (from) {
            const a = from.getBoundingClientRect();
            const start = sidePoint(a, dependencyDrag.side, boardRect, board.scrollLeft, board.scrollTop);
            const x2 = dependencyDrag.x - boardRect.left + board.scrollLeft;
            const y2 = dependencyDrag.y - boardRect.top + board.scrollTop;
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            line.setAttribute('d', `M ${start.x} ${start.y} L ${x2} ${y2}`);
            line.classList.add('dependency-line', 'dependency-line-preview');
            svg.append(line);
        }
    }
}
window.addEventListener('resize', drawDependencyLines);

function commentPanel(task) {
    const panel = element('section', 'comments-panel');
    panel.setAttribute('aria-label', `Comments for ${task.title}`);
    panel.draggable = false;
    panel.addEventListener('dragstart', event => event.preventDefault());
    const list = element('div', 'comments-list');
    (task.comments || []).forEach(comment => {
        const item = element('div', 'comment-item');
        const author = boardState.users[comment.author]?.name || comment.author;
        item.append(element('div', 'comment-meta', `${author} (${comment.author}) · ${commentTime(comment.created_at)}`));
        item.append(commentBody(comment.body));
        list.append(item);
    });
    if (!(task.comments || []).length) list.append(element('p', 'empty-comment', 'No comments yet.'));
    panel.append(list);
    const form = element('form', 'comment-form');
    const input = element('textarea', 'comment-input');
    input.required = true;
    input.maxLength = 1000;
    input.rows = 2;
    input.placeholder = 'Write a comment. Tag someone with @CR';
    input.setAttribute('aria-label', 'Write a comment');
    const hint = element('div', 'mention-hint', `Tag: ${Object.keys(boardState.users).sort().map(value => '@' + value).join('  ')}`);
    const error = element('p', 'form-error');
    const submit = element('button', 'comment-submit', 'Comment');
    submit.type = 'submit';
    form.append(input, hint, error, submit);
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || !input.value.trim()) return;
        busy = true;
        submit.disabled = true;
        error.textContent = '';
        let saved = false;
        try {
            await api(`/${task.id}/comments`, 'POST', { body: input.value });
            input.value = '';
            saved = true;
        } catch (requestError) {
            error.textContent = requestError.message;
        } finally {
            busy = false;
            submit.disabled = false;
        }
        if (saved) await refresh();
    });
    panel.append(form);
    return panel;
}
function commentBody(body) {
    const paragraph = element('p', 'comment-body');
    const mentionPattern = /(^|[^A-Za-z0-9_])@([A-Za-z]{2,3})(?![A-Za-z0-9_])/g;
    let cursor = 0;
    for (const match of body.matchAll(mentionPattern)) {
        const mentionStart = match.index + match[1].length;
        paragraph.append(document.createTextNode(body.slice(cursor, mentionStart)));
        const tag = element('strong', 'mention', '@' + match[2].toUpperCase());
        paragraph.append(tag);
        cursor = mentionStart + match[2].length + 1;
    }
    paragraph.append(document.createTextNode(body.slice(cursor)));
    return paragraph;
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
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = initials;
        checkbox.checked = selectedParticipants.has(initials);
        label.append(checkbox, `${boardState.users[initials].name || initials} (${initials})`);
        $('task-participants').append(label);
    });
    $('task-assignee').disabled = !!task?.assignee && task.assignee !== boardState.user;
    $('assignee-note').hidden = !$('task-assignee').disabled;
    $('task-deadline').value = arizonaInput(task?.deadline);
    $('task-comment').value = '';
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
        participants: Array.from(document.querySelectorAll('#task-participants input:checked')).map(el => el.value),
        deadline: $('task-deadline').value || null,
        comment: $('task-comment').value.trim() || null,
        ...(editing ? { version: editing.version } : {})
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
$('cut-dependency').onclick = () => {
    cutMode = !cutMode;
    $('cut-dependency').setAttribute('aria-pressed', String(cutMode));
    document.body.classList.toggle('cut-mode', cutMode);
    notice(cutMode ? 'Scissors active: click a dotted connection to cut it.' : '');
};
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
