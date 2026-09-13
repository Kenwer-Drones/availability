const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
(async () => {
 const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'auth-regression-'));
 const server = spawn('python', ['-c', "from server import app; app.run(port=5098, use_reloader=False)"], {
  cwd: path.resolve(__dirname, '..'), env: {...process.env, DB_PATH:path.join(temp,'test.db'), DATABASE_URL:'', SMTP_USER:''}, stdio:'ignore'
 });
 let browser;
 try {
  for(let i=0;i<100;i++){try{const response=await fetch('http://127.0.0.1:5098/auth');await response.arrayBuffer();if(response.ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH || '/tmp/chromium',args:['--no-sandbox']});
  const context=await browser.newContext({baseURL:'http://127.0.0.1:5098',timezoneId:'Asia/Kolkata'});
  await context.route('https://cdnjs.cloudflare.com/**',r=>r.abort());
  const page=await context.newPage();page.on('dialog',d=>d.accept());const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/tasks');await page.waitForURL('**/auth?next=/tasks');
  await page.click('#signup-tab');await page.fill('#name','Signup Test');await page.fill('#initials','ST');await page.fill('#password','long-password-123');await page.fill('#security-answer','tester');
  await page.selectOption('#timezone', {label:'India — Kolkata (IST) (UTC+05:30)'});
  assert.equal(await page.locator('#timezone').inputValue(), 'Asia/Kolkata');
  await page.click('#submit');await page.waitForURL('**/*created=1*');
  assert.match(await page.locator('#message').textContent(),/Account created/);
  assert.equal((await (await context.request.get('/api/auth/session')).json()).user,null);
  assert.equal(await page.locator('#initials').inputValue(),'ST');
  await page.fill('#password','long-password-123');await page.click('#submit');await page.waitForURL('**/tasks');await page.waitForFunction(()=>document.getElementById('account-name').textContent==='Signup Test');
  const headers={'X-Task-Request':'1'};
  assert.equal((await context.request.post('/api/slots',{data:{initials:'XX',date_utc:'2026-09-20',hour_utc:8}})).status(),403);
  assert.equal((await context.request.post('/api/slots',{data:{initials:'ST',date_utc:'bad-date',hour_utc:8}})).status(),400);
  assert.equal((await context.request.post('/api/timezones',{data:{initials:'XX',timezone:'America/Phoenix'}})).status(),403);
  assert.equal((await context.request.post('/api/slots',{data:{initials:'ST',date_utc:'2026-09-20',hour_utc:8}})).status(),200);
  assert.equal((await (await context.request.get('/api/auth/session')).json()).profile.timezone,'Asia/Kolkata');
  await page.goto('/');await page.waitForFunction(()=>document.getElementById('user-display').textContent.includes('Signup Test'));
  await page.reload();await page.waitForFunction(()=>document.getElementById('user-display').textContent.includes('Signup Test'));
  await page.click('#btn-reset-user');await page.waitForURL('**/auth?next=/');
  await page.fill('#initials','ST');await page.click('#forgot');await page.locator('#reset-answer').waitFor({state:'visible'});
  await page.fill('#new-password','bad');await page.click('#cancel-recovery');
  await page.fill('#password','long-password-123');await page.click('#submit');await page.waitForURL('http://127.0.0.1:5098/');
  await page.goto('/auth');await page.fill('#initials','ST');await page.click('#forgot');await page.locator('#reset-answer').waitFor({state:'visible'});
  await page.fill('#reset-answer','tester');await page.fill('#new-password','new-password-123');await page.click('#submit');await page.waitForURL('**/*reset=1*');
  assert.equal((await (await context.request.get('/api/auth/session')).json()).user,null);
  await page.fill('#password','new-password-123');await page.click('#submit');await page.waitForURL('http://127.0.0.1:5098/');
  await page.goto('/auth?mode=signup');await page.fill('#name','Duplicate');await page.fill('#initials','ST');await page.fill('#password','long-password-123');await page.fill('#security-answer','other');await page.click('#submit');await page.waitForFunction(()=>document.getElementById('message').textContent.includes('already have an account'));
  assert.equal(await page.locator('#submit').isDisabled(),false);
  await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('PASS: browser signup, sign-in, shared session, refresh, logout, recovery/cancel, duplicate signup, mobile, no JS errors');
 } finally {await browser?.close();server.kill();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
