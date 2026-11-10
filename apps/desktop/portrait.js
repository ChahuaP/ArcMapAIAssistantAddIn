import { VoiceAudio } from './speech-audio.js';

const api = window.guizhi;
const bubble = document.querySelector('#bubble');
const message = document.querySelector('#message');
const mic = document.querySelector('#mic');
const play = document.querySelector('#play');
const stop = document.querySelector('#stop');
const detail = document.querySelector('#status');
const shell = document.querySelector('main');
const mouth = document.querySelector('#speaking-mouth');
let state;
let mode = 'idle';
let held = false;
let holdId = 0;
let seen;
let task;
let conversation;
let passThrough;
let speechFrame = 0;
let mouthLevel = 0;

function tell(text, error = false) {
    detail.textContent = text;
    detail.hidden = !text;
    detail.classList.toggle('voice-error', error);
    bubble.hidden = message.hidden && detail.hidden;
}

function animateMouth() {
    mouthLevel += (voice.level() - mouthLevel) * 0.55;
    mouth.setAttribute('ry', (2.5 + mouthLevel * 6).toFixed(2));
    speechFrame = requestAnimationFrame(animateMouth);
}

const voice = new VoiceAudio(api, next => {
    mode = next;
    shell.dataset.voice = next;
    mic.setAttribute('aria-pressed', String(next === 'listening'));
    mic.title = next === 'listening' ? '松开识别' : '按住说话';
    mic.setAttribute('aria-label', mic.title);
    stop.hidden = next === 'idle';
    cancelAnimationFrame(speechFrame);
    mouthLevel = 0;
    if (next === 'speaking') speechFrame = requestAnimationFrame(animateMouth);
    tell({ opening: '正在打开麦克风', listening: '正在听', recognizing: '正在转成文字',
        synthesizing: '正在准备播报', speaking: '正在播报' }[next] || '');
});

function fail(error) {
    tell(String(error.message || error).replace(/^Error invoking remote method '[^']+': Error: /, ''), true);
}
function appearance(value) {
    document.documentElement.dataset.theme = value.dark ? 'dark' : 'light';
}

function update(next) {
    appearance(next.appearance);
    if (conversation !== undefined && next.conversation !== conversation) {
        void voice.stop().catch(fail);
    }
    conversation = next.conversation;
    state = next;
    task = next.tasks.find(item => item.id === next.focusTask);
    shell.dataset.task = task?.status || 'idle';
    message.textContent = task?.response || (task?.status === 'running' ? task.step || '正在理解任务' :
        task?.status === 'cancelling' ? '正在结束任务' : '');
    message.hidden = !message.textContent;
    bubble.hidden = message.hidden && detail.hidden;
    play.hidden = !task?.response;
    mic.disabled = next.speech.asr === 'off' || (next.speech.asr === 'local'
        ? !next.speech.providers.includes('local') : !next.speech.asrProfile.keyConfigured);
    if (seen === undefined) seen = task?.response ? task.id : '';
    if (task?.response && task.id !== seen) {
        seen = task.id;
        if (next.speech.autoSpeak && next.speech.tts !== 'off' && mode === 'idle' && !held) {
            const ready = next.speech.tts === 'local' ? next.speech.providers.includes('local')
                : next.speech.ttsProfile.keyConfigured;
            if (ready && ['succeeded', 'answered'].includes(task.status)) {
                void voice.speak(task.response, next.speech.speaker).catch(fail);
            }
        }
    }
}

async function send(text) {
    if (!text.trim()) return;
    await voice.stop();
    if (!state?.settings.workspace || window.GuizhiTaskCommand(text) === 'workbench') {
        await api.workbench();
        return;
    }
    const result = await api.submit({ prompt: text, previousTask: state.focusTask });
    update(result);
}

async function begin() {
    if (held || mic.disabled) return;
    held = true;
    const pointer = ++holdId;
    try {
        const active = await voice.listen(state?.speech.microphone, () => void end());
        if (active && (!held || pointer !== holdId)) await voice.stop();
    } catch (error) {
        held = false;
        fail(error);
    }
}

async function end() {
    if (!held) return;
    held = false;
    holdId += 1;
    try {
        const text = await voice.finish(partial => tell(partial));
        if (!text) return;
        const action = window.GuizhiTaskCommand(text);
        if (action === 'send') {
            const { prompt } = await api.readDraft();
            if (prompt?.trim()) return await api.voiceSubmit();
        }
        if (action === 'reset' || action === 'workbench') return await send(text);
        await api.voiceDraft(text);
    } catch (error) {
        fail(error);
    }
}

mic.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    mic.setPointerCapture(event.pointerId);
    void begin();
});
mic.addEventListener('pointerup', () => void end());
mic.addEventListener('pointercancel', () => { held = false; void voice.stop().catch(fail); });
mic.addEventListener('keydown', event => {
    if ((event.code === 'Space' || event.code === 'Enter') && !event.repeat) {
        event.preventDefault();
        void begin();
    }
});
mic.addEventListener('keyup', event => {
    if (event.code === 'Space' || event.code === 'Enter') {
        event.preventDefault();
        void end();
    }
});
for (const selector of ['#type', '#figure-toggle']) {
    document.querySelector(selector).addEventListener('click', () => void api.openLauncher().catch(fail));
}
document.querySelector('#open').addEventListener('click', () => void api.workbench().catch(fail));
stop.addEventListener('click', () => {
    held = false;
    void voice.stop().catch(fail);
});
play.addEventListener('click', () => {
    if (task?.response) void voice.speak(task.response, state.speech.speaker).catch(fail);
});
document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        held = false;
        void voice.stop().catch(fail);
    }
});
window.addEventListener('pagehide', () => { held = false; voice.releaseCapture(); });
document.addEventListener('pointermove', event => {
    const transparent = !event.target.closest('[data-hit]');
    if (transparent !== passThrough) {
        passThrough = transparent;
        void api.portraitHit(!transparent);
    }
});

api.onState(update);
api.onAppearance(appearance);
api.onPortraitVoice(() => mic.focus({ preventScroll: true }));
api.onPortraitHide(() => { held = false; void voice.stop().catch(fail); });
api.state().then(update).catch(fail);
