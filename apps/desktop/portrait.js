import { VoiceAudio } from './speech-audio.js';

const api = window.guizhi;
const bubble = document.querySelector('#bubble');
const message = document.querySelector('#message');
const mic = document.querySelector('#mic');
const detail = document.querySelector('#status');
const shell = document.querySelector('main');
const mouth = document.querySelector('#speaking-mouth');
const figure = document.querySelector('#figure-toggle');
let state;
let session = 0;
let sequence = 0;
let waiting;
let receiving = false;
let passThrough;
let pointer;
let moved;
let frame = 0;
let mouthLevel = 0;
let showingTask = false;

function showMessage(text) {
    message.innerHTML = window.GuizhiMarkdown.render(text);
    message.hidden = !text;
    bubble.hidden = message.hidden && detail.hidden;
}

function tell(text, error = false) {
    detail.textContent = text;
    detail.hidden = !text;
    detail.classList.toggle('voice-error', error);
    bubble.hidden = message.hidden && detail.hidden;
}

function fail(error) {
    tell(window.GuizhiErrorText(error), true);
}

function animateMouth() {
    mouthLevel += (voice.level() - mouthLevel) * 0.55;
    mouth.setAttribute('ry', (2.5 + mouthLevel * 6).toFixed(2));
    frame = requestAnimationFrame(animateMouth);
}

function paintMic() {
    mic.setAttribute('aria-pressed', String(Boolean(session)));
    const label = session ? '结束语音对话' : '语音对话';
    mic.title = label;
    mic.setAttribute('aria-label', label);
}

const voice = new VoiceAudio(api, mode => {
    shell.dataset.voice = mode;
    cancelAnimationFrame(frame);
    mouthLevel = 0;
    if (mode === 'speaking') frame = requestAnimationFrame(animateMouth);
    tell({ opening: '正在打开麦克风', listening: '我在听', recognizing: '我听到了',
        synthesizing: '让我想一下', speaking: '规智正在回答' }[mode] || '');
});

function appearance(value) {
    document.documentElement.dataset.theme = value.dark ? 'dark' : 'light';
}

function available(kind) {
    const provider = state?.speech[kind];
    return provider === 'local' ? state.speech.localAvailable
        : provider && provider !== 'off' && state.speech[kind + 'Profile'].keyConfigured;
}

async function endSession() {
    session = 0;
    waiting = null;
    receiving = false;
    paintMic();
    showMessage('');
    await voice.stop();
}

async function listen(token) {
    if (token !== session) return;
    try { await voice.listen(state.speech.microphone, () => void receive(token), true); }
    catch (error) { if (token === session) { await endSession(); fail(error); } }
}

async function reply(text, token) {
    if (token !== session) return;
    if (available('tts')) {
        try { await voice.speak(text, state.speech.speaker); }
        catch (error) { if (token === session) { showMessage(text); fail(error); } }
    } else showMessage(text);
    if (token === session) await listen(token);
}

async function receive(token) {
    if (token !== session || receiving) return;
    receiving = true;
    let result;
    try {
        const text = await voice.finish();
        if (token !== session) return;
        if (!text) { await endSession(); return; }
        showMessage('');
        tell('让我想一下');
        result = await api.voiceTurn(text);
        if (token !== session) return;
    } catch (error) {
        if (token === session) { await endSession(); fail(error); }
        return;
    } finally {
        if (token === session) receiving = false;
    }
    if (result.reply) await reply(result.reply, token);
    else {
        waiting = result.task;
        followAnswer();
    }
}

function followAnswer() {
    if (!session || !waiting) return;
    const task = state.tasks.find(item => item.id === waiting);
    if (!task) return;
    if (['running', 'cancelling'].includes(task.status)) {
        tell(task.step || '让我想一下');
        return;
    }
    waiting = null;
    const detail = { cancelled: '这一轮已经结束。', timed_out: '我这次没能及时完成。你可以再试一次。',
        missing_materials: '请先把需要办理的材料发给我。' };
    void reply(task.response || detail[task.status] || '这次没能完成。可以打开工作台看详情。', session).catch(fail);
}

function update(next) {
    appearance(next.appearance);
    state = next;
    mic.disabled = !available('asr');
    if (session && mic.disabled) void endSession().catch(fail);
    if (session) followAnswer();
    else {
        const task = state.tasks[0];
        const working = task?.status === 'running' || task?.status === 'cancelling';
        if (working || showingTask) tell(working ? task.step || '正在办理' : '');
        showingTask = working;
    }
}

function startSession() {
    if (!available('asr')) { tell('可在设置中开启语音对话。'); return; }
    if (session) return;
    session = ++sequence;
    paintMic();
    showMessage('');
    void listen(session);
}

figure.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    pointer = { id: event.pointerId, x: event.screenX, y: event.screenY };
    moved = false;
    figure.setPointerCapture(event.pointerId);
    void api.portraitDrag('start', { x: pointer.x, y: pointer.y }).catch(fail);
});
figure.addEventListener('pointermove', event => {
    if (pointer?.id !== event.pointerId) return;
    moved ||= Math.hypot(event.screenX - pointer.x, event.screenY - pointer.y) >= 5;
    if (moved) void api.portraitDrag('move', { x: event.screenX, y: event.screenY }).catch(fail);
});
function release(event) {
    if (pointer?.id !== event.pointerId) return;
    pointer = null;
    void api.portraitDrag('end').catch(fail);
}
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) figure.addEventListener(type, release);
figure.addEventListener('click', event => {
    if (!moved || event.detail === 0) void api.openLauncher().catch(fail);
});
document.querySelector('#type').addEventListener('click', () => void api.openLauncher().catch(fail));
document.querySelector('#open').addEventListener('click', () => void api.workbench().catch(fail));
message.addEventListener('click', window.GuizhiMarkdown.open);
mic.addEventListener('click', () => session ? void endSession().catch(fail) : startSession());
document.addEventListener('keydown', event => { if (event.key === 'Escape') void endSession().catch(fail); });
window.addEventListener('pagehide', () => { session = 0; voice.releaseCapture(); });
document.addEventListener('mousemove', event => {
    const transparent = !event.target.closest('[data-hit]');
    if (transparent !== passThrough) {
        passThrough = transparent;
        void api.portraitHit(!transparent).catch(fail);
    }
});

api.onState(update);
api.onAppearance(appearance);
api.onPortraitVoice(() => { mic.focus({ preventScroll: true }); startSession(); });
api.onVoiceStop(() => void endSession().catch(fail));
api.state().then(update).catch(fail);
