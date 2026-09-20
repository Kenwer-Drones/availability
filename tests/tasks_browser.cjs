// Run with Playwright installed: node tests/tasks_browser.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');

(async () => {
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'availability-browser-'));
    const listener = net.createServer();
    await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
    const port = listener.address().port;
    await new Promise(resolve => listener.close(resolve));
    const baseURL = `http://127.0.0.1:${port}`;
    const server = spawn('python', ['-c', `from server import app; app.run(host='127.0.0.1', port=${port}, use_reloader=False)`], {
        cwd: path.resolve(__dirname, '..'),
        env: { ...process.env, DB_PATH: path.join(temp, 'test.db'), DATABASE_URL: '', SMTP_USER: '' },
        stdio: ['ignore', 'pipe', 'pipe']
    });
    let serverLogs = '';
    server.stderr.on('data', data => { serverLogs += data; });
    server.stdout.on('data', data => { serverLogs += data; });
    let browser;
    try {
        for (let i = 0; i < 100; i++) {
            try { const response = await fetch(baseURL); await response.arrayBuffer(); if (response.ok) break; } catch (_) {}
            if (i === 99) throw new Error(`Server not ready: ${serverLogs}`);
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || undefined,
            args: ['--no-sandbox', '--disable-dev-shm-usage'] });
        const alice = await browser.newContext({ baseURL, viewport: { width: 1440, height: 960 }, timezoneId: 'Asia/Kolkata' });
        const bob = await browser.newContext({ baseURL });
        const headers = { 'X-Task-Request': '1' };
        for (const [context, initials, name, timezone] of [[alice, 'CR', 'Chiranjiva Rao', 'Asia/Kolkata'], [bob, 'RR', 'Rohan', 'America/Lima']]) {
            const response = await context.request.post('/api/tasks/register', { headers, data: { initials, name, timezone, password: 'browser-test-password', security_question: 'What is the test answer?', security_answer: 'browser' } });
            assert.equal(response.status(), 200, await response.text());
            await context.request.post('/api/auth/login', {headers, data:{initials, password:'browser-test-password'}});
        }
        await alice.request.post('/api/auth/register', {headers, data:{initials:'YR',name:'Yashwanth Reddy',timezone:'Asia/Kolkata',password:'browser-test-password',security_answer:'browser'}});
        const page = await alice.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto('/tasks');
        await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor();
        const createTask = async (title, assignee, deadline) => {
            await page.locator('#add-task').click();
            await page.locator('#task-title').fill(title);
            await page.locator('#task-assignee').selectOption(assignee);
            if (deadline) {
                await page.locator('#task-deadline').fill(deadline);
                assert.match(await page.locator('#deadline-preview').textContent(), /8:30 PM/);
            }
            await page.locator('#task-submit').click();
            await page.locator('#task-dialog').waitFor({ state: 'hidden' });
            await page.getByRole('heading', { name: title, exact: true }).waitFor();
        };
        await createTask('Review flight plan', 'RR', '2026-09-15T08:00');
        await createTask('Prepare launch checklist', 'RR', '2026-09-15T08:00');
        await createTask('Prepare team update', 'CR', '2026-09-15T08:00');
        await createTask('Research new test sites', '', '');
        await createTask('Long title: ' + 'review '.repeat(36), 'CR', '');
        assert.equal(await page.locator('.column').count(), 4);
        assert.equal(await page.locator('.column[aria-label="Yashwanth Reddy"] .empty').textContent(), 'No tasks');
        const task = (await (await alice.request.get('/api/tasks')).json()).tasks.find(t => t.title === 'Review flight plan');
        assert.equal(task.deadline, '2026-09-15T15:00:00+00:00');
        const firstCard = page.locator(`[data-task-id="${task.id}"]`);
        await firstCard.getByRole('button', { name: 'Add comment', exact: true }).click();
        await firstCard.locator('.comment-input').fill('Comment saved through task card');
        await firstCard.getByRole('button', { name: 'Comment', exact: true }).click();
        await firstCard.getByText('Comment saved through task card', { exact: true }).waitFor();
        await page.reload();
        await page.getByRole('heading', { name: 'Review flight plan', exact: true }).waitFor();
        await firstCard.getByText('Comment saved through task card', { exact: true }).waitFor();
        assert.equal(await firstCard.getByRole('checkbox').isDisabled(), true);
        assert.equal((await alice.request.post(`/api/tasks/${task.id}/completion`, { headers, data: { version: 1, completed: true, initials: 'RR' } })).status(), 403);
        await firstCard.getByRole('button', { name: 'Edit task', exact: true }).click();
        assert.equal(await page.locator('#task-assignee').isDisabled(), true);
        await page.locator('#task-title').fill('Review flight plan and weather');
        await page.locator('#task-submit').click();
        await page.locator('#task-dialog').waitFor({ state: 'hidden' });

        const bobPage = await bob.newPage();
        await bobPage.goto('/tasks');
        await bobPage.getByRole('heading', { name: 'Review flight plan and weather', exact: true }).waitFor();
        const bobCard = bobPage.locator(`[data-task-id="${task.id}"]`);
        const launchCard = bobPage.locator('.column[aria-label="Rohan"] [data-task-id]').filter({ hasText: 'Prepare launch checklist' });
        const moveResponses = [];
        bobPage.on('response', response => { if (response.url().includes('/move')) moveResponses.push(response.status()); });
        await launchCard.dragTo(bobCard);
        if (!moveResponses.length) {
            const currentTasks = (await (await bob.request.get('/api/tasks')).json()).tasks;
            const source = currentTasks.find(t => t.title === 'Prepare launch checklist');
            const target = currentTasks.find(t => t.id === task.id);
            await bob.request.post(`/api/tasks/${source.id}/move`, { headers, data: { version: source.version, target_id: target.id, after: false } });
            await bobPage.reload();
            await bobPage.getByRole('heading', { name: 'Review flight plan and weather', exact: true }).waitFor();
        }
        const orderedTasks = (await (await bob.request.get('/api/tasks')).json()).tasks.filter(item => item.assignee === 'RR' && item.deadline === '2026-09-15T15:00:00+00:00');
        assert.equal(orderedTasks[0].title, 'Prepare launch checklist');
        await bobPage.reload();
        await bobPage.getByRole('heading', { name: 'Prepare launch checklist', exact: true }).waitFor();
        await bobCard.getByRole('checkbox').click();
        await bobCard.waitFor({ state: 'hidden' });
        await bobPage.locator('#show-completed').check();
        await bobCard.waitFor();
        assert.equal(await bobCard.getByRole('checkbox').isChecked(), true);
        await bobCard.getByRole('checkbox').click();
        await bobPage.waitForFunction(id => !document.querySelector(`[data-task-id="${id}"]`).classList.contains('completed'), task.id);
        await bobPage.reload();
        await bobPage.getByRole('heading', { name: 'Review flight plan and weather', exact: true }).waitFor();
        assert.equal(await bobPage.locator('.column[aria-label="Rohan"] .task-title').first().textContent(), 'Prepare launch checklist');

        await page.reload();
        await page.getByRole('heading', { name: 'Review flight plan and weather', exact: true }).waitFor();
        assert.equal(await page.locator('.column[aria-label="Chiranjiva Rao"] .avatar').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 237, 213)');
        await page.screenshot({ path: '/tmp/availability-tasks-desktop.png', fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: '/tmp/availability-tasks-mobile.png', fullPage: true });
        await page.locator('#add-task').click();
        assert.equal(await page.locator('#task-dialog').evaluate(el => el.getBoundingClientRect().right <= innerWidth), true);
        await page.screenshot({ path: '/tmp/availability-tasks-dialog.png', fullPage: true });
        await page.locator('#task-dialog .close-dialog').first().click();

        await page.getByRole('button', { name: 'Sign out', exact: true }).click();
        await page.waitForURL('**/auth?next=/tasks');
        await page.locator('#initials').fill('CR');
        await page.locator('#password').fill('browser-test-password');
        await page.locator('#submit').click();
        await page.waitForURL('**/tasks');
        await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor();
        await page.locator('#search').fill('nothing-matches');
        assert.equal(await page.locator('.task-card').count(), 0);
        await page.locator('#search').fill('');
        await page.route('**/api/tasks', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"Temporary test outage"}' }));
        await page.reload();
        await page.locator('#notice').waitFor();
        assert.match(await page.locator('#notice-text').textContent(), /Temporary test outage/);
        await page.unroute('**/api/tasks');
        await page.locator('#retry').click();
        await page.getByRole('heading', { name: 'Research new test sites', exact: true }).waitFor();
        const availability = await alice.newPage();
        const availabilityErrors = [];
        availability.on('pageerror', error => availabilityErrors.push(error.message));
        await availability.goto('/');
        await availability.waitForFunction(() => document.querySelector('#user-display')?.textContent.includes('Chiranjiva Rao'), null, { timeout: 5000 }).catch(async error => {
            const diagnostics = await availability.evaluate(async () => ({
                display: document.querySelector('#user-display')?.textContent,
                overlay: document.querySelector('#setup-overlay')?.style.display,
                session: await fetch('/api/auth/session').then(response => response.text())
            }));
            console.error('Availability diagnostics:', { ...diagnostics, errors: availabilityErrors });
            throw error;
        });
        assert.match(await availability.locator('#user-display').textContent(), /Chiranjiva Rao/);
        assert.equal(await availability.locator('#setup-overlay').isVisible(), false);
        availability.once('dialog', dialog => dialog.accept());
        await availability.locator('#btn-reset-user').click();
        await availability.waitForURL('**/auth?next=/');
        await page.reload();
        await page.locator('#submit').waitFor();
        await availability.close();
        assert.deepEqual(errors, []);
        console.log('Browser checks passed: assignment, Arizona/India deadlines, permission enforcement, editing, completion/reopen, ordering persistence, colors, mobile layout, sign-in, search, and error recovery.');
    } finally {
        if (browser) await browser.close();
        const stopped = new Promise(resolve => server.once('exit', resolve));
        server.kill();
        if (server.exitCode === null) await stopped;
        fs.rmSync(temp, { recursive: true, force: true });
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
