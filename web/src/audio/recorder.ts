// Phone answer recording (PRD 14.2): MediaRecorder in the browser's best container + an AnalyserNode
// for the live waveform. The server converts the blob to 24 kHz WAV with ffmpeg.

export interface RecorderHandle {
  stop(): Promise<Blob>;
  cancel(): void;
  readonly analyser: AnalyserNode;
  readonly mimeType: string;
}

// Chrome/Android record webm/opus; iOS Safari records mp4/aac; Firefox can do ogg/opus.
const MIME_PREFERENCE = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"];
const TIMESLICE_MS = 250;

export function extForMime(mime: string): string {
  const m = mime.toLowerCase();
  if (m.includes("webm")) return "webm";
  if (m.includes("mp4") || m.includes("aac") || m.includes("m4a")) return "mp4";
  if (m.includes("ogg")) return "ogg";
  if (m.includes("wav")) return "wav";
  return "webm";
}

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") return undefined;
  return MIME_PREFERENCE.find((m) => MediaRecorder.isTypeSupported(m));
}

export async function startRecording(): Promise<RecorderHandle> {
  if (typeof MediaRecorder === "undefined") throw new Error("Recording is not supported in this browser");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

  let recorder: MediaRecorder;
  try {
    const preferred = pickMimeType();
    recorder = new MediaRecorder(stream, preferred ? { mimeType: preferred } : undefined);
  } catch (err) {
    for (const track of stream.getTracks()) track.stop();
    throw err;
  }

  const ctx = new AudioContext();
  if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.6;
  source.connect(analyser); // not connected to the speakers: no feedback

  const chunks: Blob[] = [];
  let released = false;
  let stopping: Promise<Blob> | null = null;

  const release = () => {
    if (released) return;
    released = true;
    source.disconnect();
    for (const track of stream.getTracks()) track.stop();
    void ctx.close().catch(() => undefined);
  };

  recorder.ondataavailable = (e: BlobEvent) => {
    if (e.data.size > 0) chunks.push(e.data);
  };
  recorder.start(TIMESLICE_MS);
  // Some browsers only settle on the final container after start().
  const mimeType = recorder.mimeType || pickMimeType() || "audio/webm";

  return {
    analyser,
    mimeType,
    stop() {
      if (stopping) return stopping;
      stopping = new Promise<Blob>((resolve, reject) => {
        if (recorder.state === "inactive") {
          release();
          resolve(new Blob(chunks, { type: mimeType }));
          return;
        }
        recorder.onstop = () => {
          release();
          resolve(new Blob(chunks, { type: mimeType }));
        };
        recorder.onerror = () => {
          release();
          reject(new Error("Recording failed"));
        };
        recorder.stop();
      });
      return stopping;
    },
    cancel() {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      if (recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // already stopped
        }
      }
      chunks.length = 0;
      release();
    },
  };
}
