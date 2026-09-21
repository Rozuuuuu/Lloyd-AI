/* Loads the real mobile.html in jsdom, runs its actual inline script, and
   drives the settings UI by clicking real elements. This is the behavioural
   test the browser extension would have given us. */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const HTML = String.raw`C:\Users\Home\Desktop\AI\Jarvis AI\backend\mobile.html`;
let html = fs.readFileSync(HTML, 'utf8');

// Strip the Tailwind CDN script: it cannot fetch here and we are testing our
// own logic, not Tailwind's utility classes.
html = html.replace(/<script src="https:\/\/cdn\.tailwindcss\.com"><\/script>/, '');
html = html.replace(/<script>\s*tailwind\.config[\s\S]*?<\/script>/, '');

let pass = 0, fail = 0;
const results = [];
function ok(label, cond, detail) {
  if (cond) { pass++; results.push(['PASS', label, '']); }
  else { fail++; results.push(['FAIL', label, detail || '']); }
}

const store = {};
const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'http://localhost:8090/',
  beforeParse(win) {
    // Stubs for the browser APIs this page expects but jsdom lacks.
    win.WebSocket = function () {
      this.readyState = 1; this.sent = [];
      this.send = d => this.sent.push(d);
      this.close = () => {};
      win.__ws = this;
      setTimeout(() => this.onopen && this.onopen(), 0);
    };
    win.WebSocket.OPEN = 1;
    win.HTMLCanvasElement.prototype.getContext = () => ({
      clearRect(){}, beginPath(){}, arc(){}, fill(){}, createRadialGradient(){
        return { addColorStop(){} };
      }, set fillStyle(v){}, set globalAlpha(v){},
    });
    Object.defineProperty(win, 'localStorage', {
      value: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
      }, configurable: true,
    });
    win.navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
    win.Audio = function () { this.play = async () => {}; this.pause = () => {}; };
    win.URL.createObjectURL = () => 'blob:x';
    win.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 16);
  },
});

const { window } = dom;
const doc = window.document;
const body = doc.getElementById('appBody');
const $ = s => doc.querySelector(s);
const click = el => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));

setTimeout(() => {
  // ---------- defaults ----------
  ok('defaults: all layers on',
     body.dataset.text === 'on' && body.dataset.chat === 'on' && body.dataset.mic === 'on',
     JSON.stringify(body.dataset));
  ok('defaults: font=display fs=m',
     body.dataset.font === 'display' && body.dataset.fs === 'm');
  ok('defaults: sheet closed', body.dataset.sheet === 'closed');
  ok('defaults: coreonly off', body.dataset.coreonly === 'off');

  // ---------- sheet open/close ----------
  click(doc.getElementById('gearBtn'));
  ok('gear opens sheet', body.dataset.sheet === 'open');
  ok('gear aria-expanded true',
     doc.getElementById('gearBtn').getAttribute('aria-expanded') === 'true');
  click(doc.getElementById('settingsBackdrop'));
  ok('backdrop closes sheet', body.dataset.sheet === 'closed');
  click(doc.getElementById('gearBtn'));
  click(doc.getElementById('sheetClose'));
  ok('close button closes sheet', body.dataset.sheet === 'closed');

  // ---------- individual layer toggles ----------
  const tglText = $('.tgl[data-layer="text"]');
  const tglChat = $('.tgl[data-layer="chat"]');
  const tglMic  = $('.tgl[data-layer="mic"]');

  click(tglText);
  ok('text toggle -> off', body.dataset.text === 'off');
  ok('text toggle aria-checked false', tglText.getAttribute('aria-checked') === 'false');
  ok('turning text off does not affect chat', body.dataset.chat === 'on');
  click(tglText);
  ok('text toggle -> back on', body.dataset.text === 'on');

  click(tglChat);
  ok('chat toggle -> off', body.dataset.chat === 'off');
  click(tglMic);
  ok('mic toggle -> off', body.dataset.mic === 'off');
  ok('two of three off is NOT core-only', body.dataset.coreonly === 'off');
  click(tglText);
  ok('all three off IS core-only', body.dataset.coreonly === 'on');

  // ---------- the stranding check ----------
  const gearStyle = window.getComputedStyle(doc.getElementById('gearBtn'));
  ok('gear still displayed in core-only', gearStyle.display !== 'none',
     'display=' + gearStyle.display);
  // The core promise: in core-only mode the layers must really be gone.
  const disp = id => window.getComputedStyle(doc.getElementById(id)).display;
  ok('core-only hides header', disp('chromeHeader') === 'none', disp('chromeHeader'));
  ok('core-only hides state labels', disp('stateBlock') === 'none', disp('stateBlock'));
  ok('core-only hides chat', disp('chatSection') === 'none', disp('chatSection'));
  ok('core-only hides mic meters', disp('micMeters') === 'none', disp('micMeters'));
  ok('core-only hides speak button', disp('micBtn') === 'none', disp('micBtn'));
  ok('core-only KEEPS the orb', disp('orb') !== 'none', disp('orb'));

  ok('gear is not inside a hidden layer',
     !doc.getElementById('gearBtn').closest('#chromeHeader,#stateBlock,#chatSection,#micMeters'));

  // ---------- preset both ways ----------
  const preset = doc.getElementById('corePreset');
  ok('preset reads "Show everything" in core-only',
     /everything/i.test(preset.textContent), preset.textContent.trim());
  click(preset);
  ok('preset restores every layer',
     body.dataset.text === 'on' && body.dataset.chat === 'on' && body.dataset.mic === 'on');
  ok('preset reads "Show core only" again',
     /core only/i.test(preset.textContent), preset.textContent.trim());
  click(preset);
  ok('preset hides every layer', body.dataset.coreonly === 'on');
  click(preset);

  // ---------- typography ----------
  click($('[data-font-opt="mono"]'));
  ok('font -> mono', body.dataset.font === 'mono');
  ok('mono option marked selected',
     $('[data-font-opt="mono"]').classList.contains('opt-on'));
  ok('previous font option deselected',
     !$('[data-font-opt="display"]').classList.contains('opt-on'));
  click($('[data-fs-opt="l"]'));
  ok('size -> large', body.dataset.fs === 'l');
  click($('[data-fs-opt="s"]'));
  ok('size -> small', body.dataset.fs === 's');

  // ---------- persistence ----------
  ok('settings written to localStorage', !!store['jarvis.ui.v1'],
     Object.keys(store).join(','));
  let saved = {};
  try { saved = JSON.parse(store['jarvis.ui.v1']); } catch (_) {}
  ok('saved payload matches live state',
     saved.font === 'mono' && saved.fs === 's', JSON.stringify(saved));

  // ---------- typed chat ----------
  const input = doc.getElementById('chatInput');
  const sendBtn = doc.getElementById('sendBtn');
  ok('send enabled once socket open', sendBtn.disabled === false);

  input.value = '   ';
  click(sendBtn);
  ok('whitespace-only message is not sent',
     (window.__ws.sent || []).length === 0, JSON.stringify(window.__ws.sent));

  input.value = 'what time is it';
  click(sendBtn);
  const sent = (window.__ws.sent || []).map(JSON.parse);
  ok('typed message sent over ws', sent.length === 1, JSON.stringify(sent));
  ok('payload shape matches backend contract',
     sent[0] && sent[0].type === 'user_message' && sent[0].content === 'what time is it',
     JSON.stringify(sent[0]));
  ok('input cleared after send', input.value === '');
  ok('message rendered in transcript',
     /what time is it/.test(doc.getElementById('transcript').textContent));

  // Enter key path
  input.value = 'second message';
  input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  ok('Enter key sends', (window.__ws.sent || []).length === 2);

  // ---------- restored-state boot ----------
  results.push(['----', 'reload with stored settings', '']);
  const dom2 = new JSDOM(html, {
    runScripts: 'dangerously', pretendToBeVisual: true,
    url: 'http://localhost:8090/',
    beforeParse(win) {
      win.WebSocket = function () { this.readyState = 1; this.send = () => {}; };
      win.WebSocket.OPEN = 1;
      win.HTMLCanvasElement.prototype.getContext = () => ({
        clearRect(){}, beginPath(){}, arc(){}, fill(){},
        createRadialGradient(){ return { addColorStop(){} }; },
        set fillStyle(v){}, set globalAlpha(v){},
      });
      Object.defineProperty(win, 'localStorage', {
        value: { getItem: k => (k in store ? store[k] : null),
                 setItem: (k, v) => { store[k] = String(v); },
                 removeItem: k => { delete store[k]; } },
        configurable: true });
      win.navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
      win.Audio = function () { this.play = async () => {}; this.pause = () => {}; };
      win.URL.createObjectURL = () => 'blob:x';
      win.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 16);
    },
  });
  setTimeout(() => {
    const b2 = dom2.window.document.getElementById('appBody');
    ok('font survives reload', b2.dataset.font === 'mono', b2.dataset.font);
    ok('size survives reload', b2.dataset.fs === 's', b2.dataset.fs);
    ok('sheet starts closed after reload', b2.dataset.sheet === 'closed');

    console.log('mobile.html behavioural test (jsdom)');
    console.log('-'.repeat(68));
    for (const [s, l, d] of results) {
      console.log(`${s.padEnd(4)} ${l}${d && s === 'FAIL' ? '  <- ' + d : ''}`);
    }
    console.log('-'.repeat(68));
    console.log(`${pass} passed, ${fail} failed`);
    process.exit(fail ? 1 : 0);
  }, 300);
}, 300);
