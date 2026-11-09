import { VoiceAudio } from './speech-audio.js';
import { mountAvatar } from './avatar.js';

const api = window.guizhi;
const pane = document.querySelector('#interaction');
const draft = document.querySelector('#draft');
const message = document.querySelector('#bubble');
const mic = document.querySelector('#mic');
const play = document.querySelector('#play');
const detail = document.querySelector('#status');
const shell = document.querySelector('main');
let state;
let mode = 'idle';
let held = false;
let holdId = 0;
let seen;
let task;
let conversation;
let passThrough;

function tell(text, error = false) {
    detail.textContent = text;
    detail.classList.toggle('voice-error', error);
}

const voice = new VoiceAudio(api, next => {
    mode = next;
    shell.dataset.voice = next;
    mic.setAttribute('aria-pressed', String(next === 'listening'));
    mic.textContent = next === 'listening' ? '松开识别' : '按住说话';
    tell({ opening: '正在打开麦克风', listening: '正在听', recognizing: '正在转成文字',
        synthesizing: '正在准备播报', speaking: '正在播报' }[next] || '');
});

function fail(error) {
    tell(String(error.message || error).replace(/^Error invoking remote method '[^']+': Error: /, ''), true);
}
function toggle(force) {
    pane.hidden = typeof force === 'boolean' ? !force : !pane.hidden;
    if (!pane.hidden) draft.focus({ preventScroll: true });
}

function update(next) {
    if (conversation !== undefined && next.conversation !== conversation) {
        draft.value = '';
        void voice.stop().catch(fail);
    }
    conversation = next.conversation;
    state = next;
    task = next.tasks.find(item => item.id === next.focusTask);
    shell.dataset.task = task?.status || 'idle';
    message.textContent = task?.response || (task?.status === 'running' ? task.step || '正在理解任务' :
        task?.status === 'cancelling' ? '正在结束任务' : '');
    message.hidden = !message.textContent;
    play.hidden = !task?.response;
    mic.disabled = next.speech.asr === 'off';
    if (seen === undefined) seen = task?.response ? task.id : '';
    if (task?.response && task.id !== seen) {
        seen = task.id;
        if (next.speech.autoSpeak && next.speech.tts !== 'off' && mode === 'idle' && !held) {
            const ready = next.speech.tts !== 'minimax' || next.speech.onlineReady;
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
    draft.value = '';
    toggle(false);
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
    const priorText = draft.value;
    try {
        const text = await voice.finish(partial => {
            toggle(true);
            draft.value = partial;
        });
        if (!text) return;
        const action = window.GuizhiTaskCommand(text);
        if (action === 'send' && priorText.trim()) return await send(priorText);
        if (action === 'reset' || action === 'workbench') return await send(text);
        toggle(true);
        draft.value = text;
        draft.select();
    } catch (error) {
        draft.value = priorText;
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
document.querySelector('#type').addEventListener('click', () => toggle());
document.querySelector('#figure-toggle').addEventListener('click', () => toggle());
document.querySelector('#open').addEventListener('click', () => void api.workbench().catch(fail));
document.querySelector('#stop').addEventListener('click', () => { held = false; void voice.stop().catch(fail); });
play.addEventListener('click', () => { if (task?.response) void voice.speak(task.response, state.speech.speaker).catch(fail); });
pane.addEventListener('submit', event => { event.preventDefault(); void send(draft.value).catch(fail); });
draft.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        pane.requestSubmit();
    }
});
document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        held = false;
        void voice.stop().catch(fail);
        toggle(false);
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
api.onPortraitInput(() => toggle(true));
api.onPortraitHide(() => { held = false; void voice.stop().catch(fail); });
api.state().then(update).catch(fail);
mountAvatar(document.querySelector('#figure'), () => voice.level(), () => ({
    voice: mode, task: task?.status, reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
})).catch(error => {
    document.querySelector('#figure').hidden = true;
    document.querySelector('#figure-toggle').classList.add('avatar-unavailable');
    fail(error);
});
