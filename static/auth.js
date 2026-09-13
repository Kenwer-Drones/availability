'use strict';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const next = (() => {
    const value = params.get('next') || '/';
    return value === '/' || value === '/tasks' ? value : '/';
})();
let mode = params.get('mode') === 'signup' ? 'signup' : 'login';
let recovery = false;

function setMessage(text, success = false) {
    $('message').textContent = text;
    $('message').className = success ? 'error success' : 'error';
}
function setMode(value) {
    mode = value;
    recovery = false;
    $('title').textContent = mode === 'signup' ? 'Create your account' : 'Sign in';
    $('subtitle').textContent = mode === 'signup' ? 'One account works across Availability and Tasks.' : 'Use the same account for Availability and Tasks.';
    $('login-tab').classList.toggle('active', mode === 'login');
    $('signup-tab').classList.toggle('active', mode === 'signup');
    $('signup-fields').hidden = mode !== 'signup';
    $('signup-recovery').hidden = mode !== 'signup';
    $('password-label').hidden = false;
    $('password').required = true;
    $('password').disabled = false;
    $('password').minLength = mode === 'signup' ? 10 : 1;
    for (const id of ['name', 'security-answer', 'city', 'country']) {
        $(id).disabled = mode !== 'signup';
        $(id).required = mode === 'signup';
    }
    for (const id of ['reset-answer', 'new-password']) { $(id).disabled = true; $(id).required = false; }
    $('login-tab').hidden = false;
    $('signup-tab').hidden = false;
    $('password').autocomplete = mode === 'signup' ? 'new-password' : 'current-password';
    $('forgot').hidden = mode !== 'login';
    $('reset-fields').hidden = true;
    $('cancel-recovery').hidden = true;
    $('submit').textContent = mode === 'signup' ? 'Create account' : 'Sign in';
    setMessage('');
}
async function request(path, method = 'GET', body) {
    const response = await fetch(`/api/auth${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', 'X-Task-Request': '1' },
        body: body === undefined ? undefined : JSON.stringify(body)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'Unable to complete the request.');
    return result;
}
async function openRecovery() {
    const initials = $('initials').value.trim().toUpperCase();
    if (!/^[A-Z]{2,3}$/.test(initials)) { setMessage('Enter your initials first.'); return; }
    try {
        const result = await request(`/security-question?initials=${encodeURIComponent(initials)}`);
        recovery = true;
        $('title').textContent = 'Reset your password';
        $('subtitle').textContent = 'Answer your recovery question to create a new password.';
        $('login-tab').hidden = true;
        $('signup-tab').hidden = true;
        $('signup-fields').hidden = true;
        $('signup-recovery').hidden = true;
        $('password-label').hidden = true;
        $('password').required = false;
        $('password').disabled = true;
        for (const id of ['reset-answer', 'new-password']) { $(id).disabled = false; $(id).required = true; }
        $('forgot').hidden = true;
        $('reset-fields').hidden = false;
        $('recovery-question').textContent = result.question;
        $('submit').textContent = 'Reset password';
        $('cancel-recovery').hidden = false;
        setMessage('');
        $('reset-answer').focus();
    } catch (error) { setMessage(error.message); }
}
$('login-tab').onclick = () => setMode('login');
$('signup-tab').onclick = () => setMode('signup');
$('forgot').onclick = openRecovery;
$('cancel-recovery').onclick = () => { $('login-tab').hidden = false; $('signup-tab').hidden = false; setMode('login'); };
$('auth-form').onsubmit = async event => {
    event.preventDefault();
    const button = $('submit');
    if (button.disabled) return;
    button.disabled = true;
    setMessage('');
    try {
        const initials = $('initials').value.trim().toUpperCase();
        if (recovery) {
            await request('/reset-password', 'POST', { initials, security_answer: $('reset-answer').value.trim(), new_password: $('new-password').value });
            location.href = `${location.pathname}?mode=login&next=${encodeURIComponent(next)}&reset=1&initials=${encodeURIComponent(initials)}`;
            return;
        }
        if (mode === 'signup') {
            await request('/register', 'POST', {
                initials, name: $('name').value.trim(), password: $('password').value,
                security_answer: $('security-answer').value.trim(),
                city: $('city').value.trim(), country: $('country').value.trim()
            });
            location.href = `${location.pathname}?mode=login&next=${encodeURIComponent(next)}&created=1&initials=${encodeURIComponent(initials)}`;
            return;
        }
        await request('/login', 'POST', { initials, password: $('password').value });
        location.href = next;
    } catch (error) {
        setMessage(error.message);
        button.disabled = false;
    }
};
setMode(mode);
if (params.get('created')) setMessage('Account created. Sign in to continue.', true);
if (params.get('reset')) setMessage('Password reset. Sign in to continue.', true);

$('initials').value = params.get('initials') || '';
