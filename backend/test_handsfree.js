/* Hands-free conversation tests for mobile.html.
 *
 * This exercises the most fragile code in the project: the half-duplex mic
 * gating (Notes bug 9) and the Android recognition loop (Notes bug 10). Both
 * SpeechRecognition and AudioContext are stubbed so a full turn can be driven
 * deterministically.
 *
 *   npm install jsdom
 *   node test_handsfree.js
 */
const fs = require('fs');
const { JSDOM } = require('jsdom');

const HTML = String.raw`C:\Users\Home\Desktop\AI\Jarvis AI\backend\mobile.html`;
const html = fs.readFileSync(HTML, 'utf8')
  .replace(/<script src="https:\/\/cdn\.tailwindcss\.com"><\/script>/, '')
  .replace(/<script>\s*tailwind\.config[\s\S]*?<\/script>/, '');

let pass = 0, fail = 0;
const rows = [];
function ok(label, cond, detail) {
  if (cond) { pass++; rows.push(['PASS', label, '']); }
  else { fail++; rows.push(['FAIL', label, detail === undefined ? '' : String(detail)]); }
}

// ---------------------------------------------------------------- stubs
let ctxTime = 0;
const live = [];                     // scheduled sources awaiting onended

class FakeCtx {
  constructor() { this.state = 'running'; this.destination = {}; }
  get currentTime() { return ctxTime; }
  resume() { return Promise.resolve(); }
  decodeAudioData(buf) { return Promise.resolve({ duration: buf.byteLength / 1000 }); }
  createBufferSource() {
    const src = { buffer: null, onended: null, connect() {},
                  start() { live.push(src); }, stop() { const i = live.indexOf(src); if (i >= 0) live.splice(i, 1); } };
    return src;
  }
}
function finishAudio() {                       // pretend every chunk finished
  const done = live.splice(0, live.length);
  done.forEach(s => s.onended && s.onended());
}

class FakeSR {
  constructor() {
    FakeSR.last = this;
    this.starts = 0; this.stops = 0; this.aborts = 0;
    this.continuous = null;
  }
  start() { this.starts++; if (this.onstart) this.onstart(); }
  stop() { this.stops++; }
  abort() { this.aborts++; }
  result(text) { this.onresult({ results: [[{ transcript: text }]] }); }
  end() { this.onend(); }
}

const dom = new JSDOM(html, {
  runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'http://localhost:8090/',
  beforeParse(win) {
    win.SpeechRecognition = FakeSR;
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
const ev = win => new window.MouseEvent('click', { bubbles: true });
const wait = ms => new Promise(r => setTimeout(r, ms));
const peek = expr => window.eval(expr);          // read top-level let bindings
const chunk = n => Buffer.alloc(n, 65).toString('base64');
const tglConv = key => doc.querySelector(`.tgl[data-conv="${key}"]`);

// Drive a complete assistant turn: think -> speak -> audio -> idle.
async function assistantTurn(text, audioBytes) {
  window.handle({ type: 'state', value: 'thinking' });
  window.handle({ type: 'token', content: text });
  window.handle({ type: 'state', value: 'speaking' });
  window.handle({ type: 'audio', content: chunk(audioBytes || 1000) });
  await wait(30);
  window.handle({ type: 'state', value: 'idle' });
}

(async () => {
  await wait(40);
  // Prime the mic the way a real first tap does.
  doc.body.dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
  await wait(400);

  const sr = FakeSR.last;
  ok('recognition uses continuous = false (Notes bug 10)',
     sr.continuous === false, sr.continuous);
  ok('hands-free is off by default', peek('settings.handsfree') === false);
  ok('barge-in is off by default', peek('settings.bargein') === false);

  // ---------------------------------------------- no re-arm when off
  let before = sr.starts;
  await assistantTurn('All systems nominal.');
  finishAudio();
  await wait(700);
  ok('with hands-free off, the mic does not re-arm itself',
     sr.starts === before, `starts ${before} -> ${sr.starts}`);

  // ---------------------------------------------- turn it on
  tglConv('handsfree').dispatchEvent(ev());
  ok('hands-free toggle sets the setting', peek('settings.handsfree') === true);
  ok('barge-in row is dimmed until hands-free is on',
     doc.getElementById('bargeRow').style.opacity === '1');

  // ---------------------------------------------- re-arm after a turn
  before = sr.starts;
  await assistantTurn('Understood, sir.');
  await wait(600);
  ok('does NOT re-arm while audio is still playing',
     sr.starts === before, `started during playback: ${sr.starts - before}`);
  finishAudio();
  await wait(700);
  ok('re-arms once the audio has drained', sr.starts === before + 1,
     `starts ${before} -> ${sr.starts}`);
  ok('state is listening after re-arm', peek('currentState') === 'listening');
  ok('hint tells you to just speak',
     doc.getElementById('stateHint').textContent === 'JUST SPEAK',
     doc.getElementById('stateHint').textContent);

  // ---------------------------------------------- speech keeps the loop alive
  window.__ws.sent.length = 0;
  sr.result('what time is it');
  sr.end();
  await wait(50);
  const sent = window.__ws.sent.map(JSON.parse).filter(m => m.type === 'user_message');
  ok('speech heard hands-free is sent', sent.length === 1, JSON.stringify(sent));
  ok('quiet counter resets when you actually speak', peek('quietRounds') === 0);

  // ---------------------------------------------- bounded silence
  before = sr.starts;
  for (let i = 0; i < 8 && !peek('handsfreePaused'); i++) {
    window.handle({ type: 'state', value: 'idle' });
    sr.end();                       // empty result
    await wait(60);
  }
  ok('a run of silence pauses hands-free instead of looping forever',
     peek('handsfreePaused') === true, 'quietRounds=' + peek('quietRounds'));
  const pausedStarts = sr.starts;
  await wait(700);
  ok('paused means paused: no further re-arm', sr.starts === pausedStarts,
     `starts ${pausedStarts} -> ${sr.starts}`);
  ok('hint offers a way back',
     /RESUME/.test(doc.getElementById('stateHint').textContent),
     doc.getElementById('stateHint').textContent);

  // ---------------------------------------------- tap resumes
  doc.getElementById('orb').dispatchEvent(
    new window.PointerEvent('pointerup', { bubbles: true }));
  await wait(700);
  ok('a tap resumes hands-free', peek('handsfreePaused') === false);
  ok('resuming re-arms the mic', sr.starts > pausedStarts,
     `starts ${pausedStarts} -> ${sr.starts}`);

  // ---------------------------------------------- barge-in OFF
  window.stopSpeaking();
  window.handle({ type: 'state', value: 'idle' });
  await wait(50);
  window.__ws.sent.length = 0;
  window.handle({ type: 'state', value: 'speaking' });
  window.handle({ type: 'audio', content: chunk(2000) });
  await wait(40);
  ok('mic is closed while speaking when barge-in is off',
     sr.aborts > 0, 'aborts=' + sr.aborts);
  sr.result('stop talking about that');
  ok('speech during playback is ignored when barge-in is off',
     peek('currentUtterance') === '', JSON.stringify(peek('currentUtterance')));

  // ---------------------------------------------- barge-in ON
  window.stopSpeaking();
  window.handle({ type: 'state', value: 'idle' });
  tglConv('bargein').dispatchEvent(ev());
  ok('barge-in toggle sets the setting', peek('settings.bargein') === true);
  ok('barge-in note warns about the speaker',
     /headphone/i.test(doc.getElementById('bargeNote').textContent),
     doc.getElementById('bargeNote').textContent);

  // Speak a reply, keeping the mic open.
  const abortsBefore = sr.aborts;
  window.eval('listening = true');
  window.handle({ type: 'state', value: 'thinking' });
  window.handle({ type: 'token', content: 'The weather in Cebu is warm and humid today' });
  window.handle({ type: 'state', value: 'speaking' });
  window.handle({ type: 'audio', content: chunk(4000) });
  await wait(40);
  ok('mic stays OPEN while speaking when barge-in is on',
     sr.aborts === abortsBefore, `aborts ${abortsBefore} -> ${sr.aborts}`);

  // (a) JARVIS hearing its own voice must NOT count as an interruption
  ok('audio is playing before the echo test', peek('scheduled.length') > 0,
     peek('scheduled.length'));
  sr.result('the weather in cebu is warm and humid');
  ok('ECHO of JARVIS\'s own words does not interrupt',
     peek('scheduled.length') > 0 && peek('acceptAudio') === true,
     'scheduled=' + peek('scheduled.length') + ' acceptAudio=' + peek('acceptAudio'));

  // (b) a single word is noise, not an interruption
  sr.result('um');
  ok('a single word does not interrupt',
     peek('scheduled.length') > 0 && peek('acceptAudio') === true,
     'scheduled=' + peek('scheduled.length'));

  // (c) genuine unrelated speech DOES interrupt
  sr.result('actually never mind forget it');
  await wait(30);
  ok('real speech interrupts playback',
     peek('scheduled.length') === 0, peek('scheduled.length'));
  ok('interrupting stops the REST of the reply arriving',
     peek('acceptAudio') === false, peek('acceptAudio'));

  // ---------------------------------------------- the pre-existing bug
  window.handle({ type: 'audio', content: chunk(2000) });
  await wait(40);
  ok('audio sent after an interrupt is dropped, not played',
     peek('scheduled.length') === 0, peek('scheduled.length'));

  sr.end();                                 // the interrupting utterance is sent
  await wait(50);
  ok('the interrupting words are sent as a message',
     window.__ws.sent.map(JSON.parse).some(
       m => m.type === 'user_message' && /never mind/.test(m.content)),
     JSON.stringify(window.__ws.sent.slice(-2)));
  ok('a new turn accepts audio again', peek('acceptAudio') === true);

  // ---------------------------------------------- mic denial
  window.eval("muted = true");
  window.eval('handsfreePaused = false');
  const deniedStarts = sr.starts;
  window.handle({ type: 'state', value: 'idle' });
  await wait(700);
  ok('a denied mic never gets re-armed', sr.starts === deniedStarts,
     `starts ${deniedStarts} -> ${sr.starts}`);

  console.log('mobile.html hands-free conversation');
  console.log('-'.repeat(72));
  for (const [s, l, d] of rows) {
    console.log(`${s.padEnd(4)} ${l}${d && s === 'FAIL' ? '  <- ' + d : ''}`);
  }
  console.log('-'.repeat(72));
  console.log(`${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
