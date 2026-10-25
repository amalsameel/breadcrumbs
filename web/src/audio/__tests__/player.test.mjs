// CrumbPlayer (web/src/audio/player.ts) on fake WebAudio and speechSynthesis engines.
// Run: node --import ./src/components/table/__tests__/register.mjs --test 'src/audio/__tests__/*.test.mjs'
import { test } from "node:test";
import assert from "node:assert/strict";

const PLAYER = new URL("../player.ts", import.meta.url).href;
globalThis.window = globalThis;

// --- fakes -------------------------------------------------------------------------------------
class FakeParam { constructor(v){this.value=v;this.events=[];} setValueAtTime(v,t){this.value=v;this.events.push(["set",v,t]);} linearRampToValueAtTime(v,t){this.events.push(["ramp",v,t]); this.value=v;} cancelScheduledValues(){} exponentialRampToValueAtTime(){} }
class FakeNode { connect(n){return n;} disconnect(){} }
class FakeGain extends FakeNode { constructor(){super();this.gain=new FakeParam(1);} }
class FakeAnalyser extends FakeNode { constructor(){super();this.fftSize=1024;} getFloatTimeDomainData(b){b.fill(0.25);} }
class FakeSource extends FakeNode { constructor(ctx){super();this.ctx=ctx;this.onended=null;this.started=false;this.stopAt=null;} start(){this.started=true;this.ctx.sources.push(this);} stop(t){this.stopAt=t??0;} end(){this.onended?.();} }
class FakeCtx { constructor(){this.state="running";this.currentTime=0;this.sources=[];this.listeners={};this.destination=new FakeNode();this.decoded=0;}
  createBufferSource(){return new FakeSource(this);} createGain(){return new FakeGain();} createAnalyser(){return new FakeAnalyser();}
  createOscillator(){return Object.assign(new FakeNode(),{frequency:{value:0},start(){},stop(){}});}
  createBuffer(ch,frames,rate){const data=[...Array(ch)].map(()=>new Float32Array(frames));return {numberOfChannels:ch,length:frames,sampleRate:rate,duration:frames/rate,getChannelData:(c)=>data[c]};}
  decodeAudioData(){this.decoded++; return Promise.reject(new Error("bad"));}
  resume(){this.state="running";return Promise.resolve();}
  addEventListener(n,f){(this.listeners[n]??=[]).push(f);} removeEventListener(n,f){this.listeners[n]=(this.listeners[n]??[]).filter(x=>x!==f);} }

class FakeSynth { constructor(voices){this._voices=voices;this.spoken=[];this.speaking=false;this.pending=false;this.cancels=0;this.listeners={};}
  getVoices(){return this._voices;} speak(u){this.spoken.push(u);this.speaking=true;setTimeout(()=>u.onstart?.(),1);} cancel(){this.cancels++;this.speaking=false;}
  pause(){} resume(){} addEventListener(n,f){(this.listeners[n]??=[]).push(f);} removeEventListener(n,f){this.listeners[n]=(this.listeners[n]??[]).filter(x=>x!==f);}
  finishOne(){const u=this.spoken.at(-1); u.onend?.();} }
globalThis.SpeechSynthesisUtterance = class { constructor(t){this.text=t;} };

function wav(seconds, rate=24000){ const n=Math.floor(seconds*rate); const buf=Buffer.alloc(44+n*2);
  buf.write("RIFF",0);buf.writeUInt32LE(36+n*2,4);buf.write("WAVE",8);buf.write("fmt ",12);buf.writeUInt32LE(16,16);buf.writeUInt16LE(1,20);buf.writeUInt16LE(1,22);buf.writeUInt32LE(rate,24);buf.writeUInt32LE(rate*2,28);buf.writeUInt16LE(2,32);buf.writeUInt16LE(16,34);buf.write("data",36);buf.writeUInt32LE(n*2,40);
  for(let i=0;i<n;i++) buf.writeInt16LE(i%2?16384:-16384,44+i*2); return buf.toString("base64"); }
const tick=(ms=5)=>new Promise(r=>setTimeout(r,ms));
function cbs(){ const log=[]; return {log, cb:{onStart:()=>log.push("start"), onEnd:(i)=>log.push(i?"end:interrupted":"end")}}; }

const voice = (name, lang="en-US", localService=true) => ({name, lang, localService, default:false, voiceURI:name});

const mod = await import(PLAYER);

test("PCM16 WAV is copied into a buffer (no decodeAudioData) and plays to its natural end", async () => {
  globalThis.speechSynthesis = new FakeSynth([]);
  const ctx = new FakeCtx(); const p = new mod.CrumbPlayer(ctx); const {log, cb} = cbs();
  const done = p.play("c1", wav(0.5), "Hello there.", cb); await tick();
  assert.equal(ctx.decoded, 0); assert.equal(ctx.sources.length, 1);
  const src = ctx.sources[0]; assert.ok(Math.abs(src.buffer.duration-0.5)<1e-6);
  assert.equal(src.buffer.getChannelData(0)[0], -0.5);
  assert.equal(p.outputKind, "audio"); assert.equal(p.outputLevel(), 0.25);
  assert.deepEqual(log, ["start"]); src.end(); await done;
  assert.deepEqual(log, ["start","end"]); assert.equal(p.playing, false);
});

test("audio watchdog ends a line whose ended event never fires", async () => {
  const ctx = new FakeCtx(); const p = new mod.CrumbPlayer(ctx); const {log, cb} = cbs();
  await p.play("c1", wav(0.05), "Hi.", cb); // resolves via watchdog at 50 ms + 1500 ms
  assert.deepEqual(log, ["start","end"]);
});

test("closed context ends the line as interrupted", async () => {
  const ctx = new FakeCtx(); const p = new mod.CrumbPlayer(ctx); const {log, cb} = cbs();
  const done = p.play("c1", wav(2), "Hi.", cb); await tick();
  ctx.state = "closed"; ctx.listeners.statechange.forEach(f=>f()); await done;
  assert.deepEqual(log, ["start","end:interrupted"]);
});

test("fadeOut ramps and ends even if the context never fires ended", async () => {
  const ctx = new FakeCtx(); const p = new mod.CrumbPlayer(ctx); const {log, cb} = cbs();
  const done = p.play("c1", wav(2), "Hi.", cb); await tick();
  p.fadeOut(100); await done; assert.deepEqual(log, ["start","end:interrupted"]);
  assert.ok(ctx.sources[0].stopAt > 0);
});

test("speech fallback waits for voices, picks a natural voice and speaks the whole line", async () => {
  const synth = new FakeSynth([]); globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const {log, cb} = cbs();
  const done = p.play("c1", "", "Maya, tell Dev about the last trip you took.", cb);
  await tick(20); assert.equal(synth.spoken.length, 0, "still waiting for voiceschanged");
  synth._voices = [voice("Albert"), voice("Fred"), voice("Samantha"), voice("Ava (Premium)"), voice("Thomas","fr-FR")];
  synth.listeners.voiceschanged.forEach(f=>f()); await tick(20);
  assert.equal(synth.spoken.length, 1); const u = synth.spoken[0];
  assert.equal(u.voice.name, "Ava (Premium)"); assert.equal(u.text, "Maya, tell Dev about the last trip you took.");
  assert.equal(p.outputKind, "speech"); assert.equal(p.outputLevel(), null);
  assert.deepEqual(log, ["start"]); synth.finishOne(); await done; assert.deepEqual(log, ["start","end"]);
});

test("long text is spoken in chunks, all of them", async () => {
  const synth = new FakeSynth([voice("Google US English","en-US",false)]); globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const {log, cb} = cbs();
  const text = Array.from({length:6},(_,i)=>`Sentence number ${i} is here and it talks about something long enough.`).join(" ");
  const done = p.play("c1", "", text, cb); await tick(10);
  for (let i=0;i<20 && !log.includes("end");i++){ synth.finishOne(); await tick(2); }
  await done; const joined = synth.spoken.map(u=>u.text).join(" ");
  assert.ok(synth.spoken.length > 1); assert.equal(joined, text); assert.ok(synth.spoken.every(u=>u.text.length<=180));
});

test("an outside cancel (interrupted error) retries the chunk instead of dropping the line", async () => {
  const synth = new FakeSynth([voice("Samantha")]); globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const {log, cb} = cbs();
  const done = p.play("c1", "", "Hello Maya.", cb); await tick(10);
  synth.spoken[0].onerror({error:"interrupted"}); await tick(100);
  assert.equal(synth.spoken.length, 2); assert.equal(synth.spoken[1].text, "Hello Maya.");
  assert.ok(!log.includes("end")); synth.finishOne(); await done; assert.deepEqual(log, ["start","end"]);
});

test("speaking after a busy engine cancels then settles before speak", async () => {
  const synth = new FakeSynth([voice("Samantha")]); synth.speaking = true; globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const {cb} = cbs();
  p.play("c1", "", "Hi.", cb); await tick(1);
  assert.equal(synth.cancels, 1); assert.equal(synth.spoken.length, 0); await tick(80); assert.equal(synth.spoken.length, 1);
  p.fadeOut();
});

test("fadeOut on speech cancels at once; a superseding play ends the old line as interrupted", async () => {
  const synth = new FakeSynth([voice("Samantha")]); globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const a = cbs(), b = cbs();
  const d1 = p.play("c1", "", "First line.", a.cb); await tick(80); // past the settle after the last test's cancel
  const d2 = p.play("c2", "", "Second line.", b.cb); await d1; assert.deepEqual(a.log, ["start","end:interrupted"]);
  await tick(100); assert.equal(synth.spoken.at(-1).text, "Second line.");
  p.fadeOut(); await d2; assert.deepEqual(b.log, ["start","end:interrupted"]);
});

// Chrome drops a speak() issued within CANCEL_SETTLE_MS of cancel(), and cancel() clears `speaking` at
// once, so the settle wait must count from the player's own cancel too (fadeOut() then play()).
class ChromeLikeSynth extends FakeSynth {
  constructor(voices){ super(voices); this.dropped=0; this.lastCancel=-1e9; }
  cancel(){ super.cancel(); this.lastCancel=performance.now(); }
  speak(u){ if (performance.now()-this.lastCancel < 60){ this.dropped++; return; } super.speak(u); }
}

test("a line that supersedes a faded browser-voice line is still voiced (cancel-settle race)", async () => {
  const synth = new ChromeLikeSynth([voice("Samantha")]); globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const a = cbs(), b = cbs();
  const d1 = p.play("c1", "", "First line here.", a.cb); await tick(20);
  p.fadeOut(); // what speakLine does (SUPERSEDE_FADE_MS) right before the next play()
  await d1;
  const d2 = p.play("c2", "", "Second line, the one the user should hear.", b.cb); await tick(120);
  assert.equal(synth.dropped, 0);
  assert.equal(synth.spoken.at(-1).text, "Second line, the one the user should hear.");
  assert.equal(p.outputKind, "speech");
  synth.finishOne(); await d2; assert.deepEqual(b.log, ["start","end"]);
});

test("a play() right after another play() of a speech line waits for the settle too", async () => {
  const synth = new ChromeLikeSynth([voice("Samantha")]); globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const a = cbs(), b = cbs();
  p.play("c1", "", "First line.", a.cb); await tick(20);
  const d2 = p.play("c2", "", "Second line.", b.cb); await tick(120);
  assert.equal(synth.dropped, 0); assert.equal(synth.spoken.at(-1).text, "Second line.");
  synth.finishOne(); await d2; assert.deepEqual(b.log, ["start","end"]);
});

test("no settle wait when nothing was cancelled recently", async () => {
  await tick(80); // let earlier tests' cancels age out
  const synth = new FakeSynth([voice("Samantha")]); globalThis.speechSynthesis = synth;
  const p = new mod.CrumbPlayer(new FakeCtx()); const {cb} = cbs();
  p.play("c1", "", "Hi.", cb); await tick(5);
  assert.equal(synth.spoken.length, 1);
  p.fadeOut();
});

test("no speech engine: caption timer still starts and ends", async () => {
  delete globalThis.speechSynthesis;
  const p = new mod.CrumbPlayer(new FakeCtx()); const {log, cb} = cbs();
  await p.play("c1", "", "Hi.", cb); assert.deepEqual(log, ["start","end"]);
});

test("voice scoring and chunking helpers", () => {
  assert.ok(mod.scoreVoice(voice("Microsoft Aria Online (Natural) - English (United States)")) > mod.scoreVoice(voice("Samantha")));
  assert.equal(mod.scoreVoice(voice("Zarvox")), -1);
  assert.equal(mod.scoreVoice(voice("Amelie","fr-CA")), -1);
  assert.equal(mod.pickNaturalVoice([voice("Bells")]), null);
  assert.deepEqual(mod.splitForSpeech("Short."), ["Short."]);
});
