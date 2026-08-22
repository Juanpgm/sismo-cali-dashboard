// Shared tutorial-video recorder.
//
// A tutorial is a list of steps: { text, action }. For each step the runner
// shows `text` in an on-page subtitle bar, speaks it with edge-tts
// (es-CO-SalomeNeural), runs `action(page)`, and waits until the narration
// finishes. Wall-clock offsets are captured per step so the audio clips are
// later placed on the timeline exactly where their step started — actions of
// any duration stay in sync.
//
// Output per video: <name>.mp4 (H.264 + AAC, subtitles burned in on-page)
// and <name>.srt (same text, for players that want toggleable captions).
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const REPO = 'C:/Users/User/Documents/workspace/seismic_disaster_data_analisys_cali';
const PY = path.join(REPO, '.venv/Scripts/python.exe');
const FFMPEG = 'C:/Users/User/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-9.0-full_build/bin/ffmpeg.exe';
const FFPROBE = FFMPEG.replace(/ffmpeg\.exe$/, 'ffprobe.exe');
export const VOICE = 'es-CO-SalomeNeural';

export function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], ...opts });
    let out = '';
    let err = '';
    p.stdout.on('data', (d) => { out += d; });
    p.stderr.on('data', (d) => { err += d; });
    p.on('close', (code) => (code === 0 ? resolve(out) : reject(new Error(`${cmd} ${args[0]} -> ${code}\n${err.slice(-800)}`))));
  });
}

export async function tts(text, outFile) {
  await run(PY, ['-m', 'edge_tts', '--voice', VOICE, '--rate=-4%', '--text', text, '--write-media', outFile]);
}

export async function audioDurationMs(file) {
  const out = await run(FFPROBE, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', file]);
  return Math.round(parseFloat(out) * 1000);
}

/** Fake cursor + subtitle bar. The nodes are created LAZILY (on first use /
 *  first mouse move), never at document start: nodes appended by an init
 *  script can be dropped when the parser builds the real <html> tree. */
export const OVERLAY_INIT = `(() => {
  if (window.__ensureTut) return;
  window.__ensureTut = () => {
    if (document.getElementById('__tut-sub') || !document.body) return;
    const style = document.createElement('style');
    style.textContent = \`
      #__tut-cursor { position: fixed; z-index: 2147483646; width: 22px; height: 22px;
        border-radius: 50%; background: rgba(255,196,0,.45); border: 2.5px solid #FFC400;
        pointer-events: none; transform: translate(-50%,-50%); left: -100px; top: -100px;
        transition: left .05s linear, top .05s linear; box-shadow: 0 0 10px rgba(0,0,0,.4); }
      #__tut-cursor.__click { background: rgba(255,196,0,.95); }
      #__tut-sub { position: fixed; z-index: 2147483647; left: 50%; bottom: 26px;
        transform: translateX(-50%); max-width: min(920px, 92vw); width: max-content;
        background: rgba(8,14,26,.92); color: #fff; border: 1px solid rgba(255,196,0,.5);
        border-radius: 12px; padding: 13px 22px; font: 600 19px/1.45 'Segoe UI', system-ui, sans-serif;
        text-align: center; pointer-events: none; box-shadow: 0 6px 24px rgba(0,0,0,.45); }
      #__tut-sub:empty { display: none; }\`;
    const cur = document.createElement('div'); cur.id = '__tut-cursor';
    const sub = document.createElement('div'); sub.id = '__tut-sub';
    document.body.append(style, cur, sub);
  };
  addEventListener('mousemove', (e) => {
    window.__ensureTut();
    const cur = document.getElementById('__tut-cursor');
    if (cur) { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; }
  }, true);
  addEventListener('mousedown', () => document.getElementById('__tut-cursor')?.classList.add('__click'), true);
  addEventListener('mouseup', () => setTimeout(() => document.getElementById('__tut-cursor')?.classList.remove('__click'), 180), true);
})();`;

export async function setSubtitle(page, text) {
  await page.evaluate((t) => {
    window.__ensureTut?.();
    const el = document.getElementById('__tut-sub');
    if (el) el.textContent = t;
  }, text).catch(() => {});
}

const msToSrt = (ms) => {
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${p(Math.floor(ms / 3600000))}:${p(Math.floor(ms / 60000) % 60)}:${p(Math.floor(ms / 1000) % 60)},${p(ms % 1000, 3)}`;
};

/**
 * Runs the steps against `page`, narrating each one.
 * Returns the cues [{ text, startMs, endMs }] relative to `t0`.
 */
export async function runSteps(page, steps, clipsDir, t0) {
  const cues = [];
  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    const clip = path.join(clipsDir, `step-${String(i).padStart(2, '0')}.mp3`);
    const durMs = await audioDurationMs(clip);
    const startMs = Date.now() - t0;
    await setSubtitle(page, step.text);
    const minEnd = Date.now() + durMs + 500; // narration + breathing room
    if (step.action) await step.action(page);
    const left = minEnd - Date.now();
    if (left > 0) await page.waitForTimeout(left);
    cues.push({ text: step.text, startMs, endMs: Date.now() - t0 });
  }
  await setSubtitle(page, '');
  await page.waitForTimeout(900);
  return cues;
}

/** Pre-generates all narration clips (so recording never waits on the network). */
export async function synthesize(steps, clipsDir) {
  fs.mkdirSync(clipsDir, { recursive: true });
  for (let i = 0; i < steps.length; i += 1) {
    const clip = path.join(clipsDir, `step-${String(i).padStart(2, '0')}.mp3`);
    if (!fs.existsSync(clip)) await tts(steps[i].text, clip);
  }
}

/** Places each clip at its cue offset over the recorded video; writes mp4 + srt. */
export async function mux(videoWebm, cues, clipsDir, outMp4) {
  const inputs = ['-i', videoWebm];
  const delays = [];
  const labels = [];
  cues.forEach((c, i) => {
    inputs.push('-i', path.join(clipsDir, `step-${String(i).padStart(2, '0')}.mp3`));
    delays.push(`[${i + 1}]adelay=${c.startMs}|${c.startMs}[a${i}]`);
    labels.push(`[a${i}]`);
  });
  const filter = `${delays.join(';')};${labels.join('')}amix=inputs=${cues.length}:normalize=0[mix]`;
  await run(FFMPEG, [
    '-y', ...inputs,
    '-filter_complex', filter,
    '-map', '0:v', '-map', '[mix]',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '21', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', '160k',
    '-movflags', '+faststart',
    outMp4,
  ]);
  const srt = cues.map((c, i) => `${i + 1}\n${msToSrt(c.startMs)} --> ${msToSrt(c.endMs)}\n${c.text}\n`).join('\n');
  fs.writeFileSync(outMp4.replace(/\.mp4$/, '.srt'), srt, 'utf8');
}
