/* Gapless-playback test for mobile.html.
 *
 * The claim being tested: consecutive speech chunks are scheduled so that each
 * one starts at exactly the sample where the previous one ends, with no gap.
 * A stubbed AudioContext records every start() time, so the spacing can be
 * asserted numerically instead of judged by ear.
 *
 *   npm install jsdom
 *   node test_audio.js
 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const HTML = String.raw`C:\Users\Home\Desktop\AI\Jarvis AI\backend\mobile.html`;
let html = fs.readFileSync(HTML, 'utf8')
  .replace(/<script src="https:\/\/cdn\.tailwindcss\.com"><\/script>/, '')
  .replace(/<script>\s*tailwind\.config[\s\S]*?<\/script>/, '');

let pass = 0, fail = 0;
const rows = [];
function ok(label, cond, detail) {
  if (cond) { pass++; rows.push(['PASS', label, '']); }
  else { fail++; rows.push(['FAIL', label, detail === undefined ? '' : String(detail)]); }
}
const near = (a, b, tol) => Math.abs(a - b) <= (tol === undefined ? 1e-6 : tol);

// ---------------------------------------------------------------- stubs
const started = [];          // {at, duration}
const stopped = [];
let ctxTime = 0;             // the fake audio clock; never advances on its own

class FakeCtx {
  constructor() { this.state = 'running'; this.destination = {}; }
  get currentTime() { return ctxTime; }
  resume() { this.state = 'running'; return Promise.resolve(); }
  // Duration is derived from byte length so different chunks differ in length.
  decodeAudioData(arrbuf) {
    return Promise.resolve({ duration: arrbuf.byteLength / 1000, __len: arrbuf.byteLength });
  }
  createBufferSource() {
    const src = {
      buffer: null, onended: null,
      connect() {},
      start(at) { started.push({ at, duration: src.buffer.duration, src }); },
      stop() { stopped.push(src); },
    };
    return src;
  }
}

const dom = new JSDOM(html, {
  runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'http://localhost:8090/',
  beforeParse(win) {
    win.AudioContext = FakeCtx;
    win.WebSocket = function () {
      this.readyState = 1; this.sent = [];
      this.send = d => this.sent.push(d);
      this.close = () => {};
      win.__ws = this;
      setTimeout(() => this.onopen && this.onopen(), 0);
    };
    win.WebSocket.OPEN = 1;
    win.HTMLCanvasElement.prototype.getContext = () => ({
      clearRect(){}, beginPath(){}, arc(){}, fill(){},
      createRadialGradient(){ return { addColorStop(){} }; },
      set fillStyle(v){}, set globalAlpha(v){},
    });
    const store = {};
    Object.defineProperty(win, 'localStorage', {
      value: { getItem: k => (k in store ? store[k] : null),
               setItem: (k, v) => { store[k] = String(v); },
               removeItem: k => { delete store[k]; } }, configurable: true });
    win.navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
    win.Audio = function () { this.play = async () => {}; this.pause = () => {}; };
    win.URL.createObjectURL = () => 'blob:x';
    win.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 16);
  },
});

const { window } = dom;
const doc = window.document;
const tick = () => new Promise(r => setTimeout(r, 30));

// base64 of n bytes -> decodes to a buffer of n/1000 seconds
const chunk = n => Buffer.alloc(n, 65).toString('base64');

(async () => {
  await tick();

  // ------------------------------------------------ gapless scheduling
  ctxTime = 10;
  window.enqueueAudio(chunk(2000));   // 2.0s
  await tick();
  ok('first chunk scheduled', started.length === 1, started.length);
  ok('first chunk waits for the lookahead, not the raw clock',
     started[0] && started[0].at > 10 && started[0].at <= 10.2,
     started[0] && started[0].at);

  window.enqueueAudio(chunk(1500));   // 1.5s
  await tick();
  window.enqueueAudio(chunk(3000));   // 3.0s
  await tick();
  ok('all three chunks scheduled', started.length === 3, started.length);

  // The whole point: chunk N+1 begins exactly where chunk N ends.
  let contiguous = true, worst = 0;
  for (let i = 1; i < started.length; i++) {
    const expected = started[i - 1].at + started[i - 1].duration;
    const gap = started[i].at - expected;
    worst = Math.max(worst, Math.abs(gap));
    if (!near(started[i].at, expected)) contiguous = false;
  }
  ok('consecutive chunks are exactly contiguous (zero gap)', contiguous,
     'worst deviation ' + worst + 's');
  ok('worst scheduling gap is below one millisecond', worst < 0.001, worst);

  // Decoding does not serialise behind playback: all three were scheduled while
  // the clock never moved, i.e. before any of them finished playing.
  ok('chunks scheduled ahead of playback, not after it',
     started[2].at > ctxTime, started[2].at + ' vs clock ' + ctxTime);

  // ------------------------------------------------ interrupt
  window.stopSpeaking();
  ok('interrupt stops every scheduled source', stopped.length === 3, stopped.length);

  started.length = 0; stopped.length = 0;
  ctxTime = 100;
  window.enqueueAudio(chunk(1000));
  await tick();
  ok('clock restarts cleanly after an interrupt',
     started.length === 1 && started[0].at > 100 && started[0].at <= 100.2,
     started[0] && started[0].at);

  // A chunk that arrives mid-decode when the user interrupts must be dropped.
  started.length = 0;
  window.enqueueAudio(chunk(1000));   // decode in flight
  window.stopSpeaking();              // interrupt before it resolves
  await tick();
  ok('a chunk decoded after an interrupt is discarded', started.length === 0,
     started.length);

  window.stopSpeaking();

  // ------------------------------------------------ speech_config -> UI
  window.handle({ type: 'speech_config', voice: 'alan',
                  voices: ['alan', 'northern_english_male'],
                  rate: 1, rate_min: 0.6, rate_max: 1.7 });
  const slider = doc.getElementById('rateSlider');
  const voiceOpts = doc.getElementById('voiceOpts');
  ok('slider bounds come from the server',
     slider.min === '0.6' && slider.max === '1.7', slider.min + '-' + slider.max);
  ok('rate label reads Normal at 1.0',
     doc.getElementById('rateValue').textContent === 'Normal',
     doc.getElementById('rateValue').textContent);
  ok('a button is rendered per voice', voiceOpts.children.length === 2,
     voiceOpts.children.length);
  ok('active voice is marked selected',
     voiceOpts.children[0].classList.contains('opt-on'));
  ok('underscores are shown as words',
     /northern english male/.test(voiceOpts.textContent), voiceOpts.textContent);

  // ------------------------------------------------ controls -> server
  window.__ws.sent.length = 0;
  slider.value = '1.35';
  slider.dispatchEvent(new window.Event('input', { bubbles: true }));
  ok('dragging updates the label only',
     doc.getElementById('rateValue').textContent === '+35%' &&
     window.__ws.sent.length === 0,
     doc.getElementById('rateValue').textContent + ' sent=' + window.__ws.sent.length);

  slider.dispatchEvent(new window.Event('change', { bubbles: true }));
  const rateMsg = window.__ws.sent.map(JSON.parse).find(m => m.type === 'set_rate');
  ok('releasing the slider sends set_rate', !!rateMsg, JSON.stringify(window.__ws.sent));
  ok('set_rate carries the chosen value', rateMsg && near(rateMsg.value, 1.35, 1e-9),
     rateMsg && rateMsg.value);

  window.__ws.sent.length = 0;
  voiceOpts.children[1].dispatchEvent(
    new window.MouseEvent('click', { bubbles: true }));
  const vMsg = window.__ws.sent.map(JSON.parse).find(m => m.type === 'set_voice');
  ok('clicking a voice sends set_voice', !!vMsg, JSON.stringify(window.__ws.sent));
  ok('set_voice carries the voice name',
     vMsg && vMsg.name === 'northern_english_male', vMsg && vMsg.name);

  // ------------------------------------------------ report
  console.log('mobile.html gapless audio + speech controls');
  console.log('-'.repeat(70));
  for (const [s, l, d] of rows) {
    console.log(`${s.padEnd(4)} ${l}${d && s === 'FAIL' ? '  <- ' + d : ''}`);
  }
  console.log('-'.repeat(70));
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
