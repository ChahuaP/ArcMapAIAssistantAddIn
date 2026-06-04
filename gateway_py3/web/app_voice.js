    let voiceRecorder = null;
    let voiceStream = null;
    let voiceChunks = [];
    let voiceBusy = false;
    let voiceCorrectionBusy = false;
    let lastVoiceRawText = '';
    let lastVoiceCorrectedText = '';

    async function toggleVoiceInput() {
      if (voiceBusy || voiceCorrectionBusy) return;
      if (voiceRecorder && voiceRecorder.state === 'recording') {
        voiceRecorder.stop();
        return;
      }
      await startVoiceInput();
    }

    async function startVoiceInput() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        setStatus('当前浏览器不支持录音。请用 http://127.0.0.1:8765 打开 GeoPilot，或升级浏览器。');
        return;
      }
      voiceChunks = [];
      try {
        voiceStream = await navigator.mediaDevices.getUserMedia({audio: true});
        voiceRecorder = new MediaRecorder(voiceStream, mediaRecorderOptions());
        voiceRecorder.ondataavailable = event => {
          if (event.data && event.data.size > 0) voiceChunks.push(event.data);
        };
        voiceRecorder.onstop = finishVoiceInput;
        voiceRecorder.start();
        setVoiceButton('recording', '停止');
        setStatus('正在录音，说完后点击“停止”。');
      } catch (err) {
        stopVoiceTracks();
        setVoiceButton('', '语音');
        setStatus(`无法启动麦克风：${err.message || err}`);
      }
    }

    function mediaRecorderOptions() {
      const candidates = [
        'audio/webm;codecs=opus',
        'audio/ogg;codecs=opus',
        'audio/webm',
        'audio/ogg'
      ];
      for (const mimeType of candidates) {
        if (MediaRecorder.isTypeSupported(mimeType)) return {mimeType};
      }
      return {};
    }

    async function finishVoiceInput() {
      const mimeType = (voiceRecorder && voiceRecorder.mimeType) || 'audio/webm';
      stopVoiceTracks();
      if (!voiceChunks.length) {
        setVoiceButton('', '语音');
        setStatus('没有录到声音。');
        return;
      }
      voiceBusy = true;
      setVoiceButton('busy', '识别中');
      setStatus('正在识别语音...');
      try {
        const blob = new Blob(voiceChunks, {type: mimeType});
        const audioDataUri = await blobToDataUri(blob);
        const data = await api('/voice/transcribe', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            audio_data_uri: audioDataUri
          })
        });
        const text = String(data.text || data.raw_text || '').trim();
        applyVoiceText(text);
        lastVoiceRawText = text;
        lastVoiceCorrectedText = '';
        renderVoiceComparison(lastVoiceRawText, lastVoiceCorrectedText);
        setStatus('语音已识别。');
      } catch (err) {
        setStatus(err.message);
      } finally {
        voiceBusy = false;
        voiceChunks = [];
        setVoiceButton('', '语音');
      }
    }

    async function correctVoiceText() {
      if (voiceBusy || voiceCorrectionBusy) return;
      const input = document.getElementById('command');
      const text = String((input && input.value) || lastVoiceRawText || '').trim();
      if (!text) {
        setStatus('没有可校验的语音文本。');
        return;
      }
      voiceCorrectionBusy = true;
      setVoiceCorrectionButton(true);
      setVoiceButton('busy', '语音');
      setStatus('正在校验语音文本...');
      try {
        const data = await api('/voice/correct', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            text,
            mode: currentMode,
            context: latestArcgisContext || {}
          })
        });
        const raw = String(data.raw_text || text).trim();
        const corrected = String(data.text || raw).trim();
        lastVoiceRawText = raw;
        lastVoiceCorrectedText = corrected;
        applyVoiceText(corrected);
        renderVoiceComparison(lastVoiceRawText, lastVoiceCorrectedText);
        setStatus(corrected && corrected !== raw ? '语音文本已校验并更新输入框。' : '语音文本已校验。');
      } catch (err) {
        setStatus(err.message);
      } finally {
        voiceCorrectionBusy = false;
        setVoiceCorrectionButton(false);
        if (!voiceBusy) setVoiceButton('', '语音');
      }
    }

    function applyVoiceText(text) {
      const input = document.getElementById('command');
      input.value = String(text || '').trim();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      updateMentionMenu();
    }

    function renderVoiceComparison(rawText, correctedText) {
      const box = document.getElementById('voiceCompare');
      if (!box) return;
      const raw = String(rawText || '').trim();
      const corrected = String(correctedText || '').trim();
      if (!raw && !corrected) {
        box.hidden = true;
        box.innerHTML = '';
        return;
      }
      box.hidden = false;
      const hasCorrection = Boolean(corrected);
      const title = hasCorrection ? '已校验' : '已识别';
      const subtitle = hasCorrection ? '输入框已更新' : '输入框已填入识别文本';
      const correctionText = hasCorrection ? corrected : '未校验';
      const actionText = voiceCorrectionBusy ? '校验中' : (hasCorrection ? '重新校验' : '校验文本');
      box.innerHTML = `
        <div class="voice-panel-head">
          <div class="voice-panel-title">
            <strong>${title}</strong>
            <span>${subtitle}</span>
          </div>
          <div class="voice-panel-actions">
            <button id="voiceCorrectButton" class="ghost small" type="button" onclick="correctVoiceText()"${voiceCorrectionBusy ? ' disabled' : ''}>${actionText}</button>
            <button class="ghost small" type="button" onclick="closeVoiceComparison()" aria-label="关闭语音结果">关闭</button>
          </div>
        </div>
        <div class="voice-transcript-grid">
          <section class="voice-transcript">
            <span>识别文本</span>
            <p>${escapeHtml(raw || '无')}</p>
          </section>
          <section class="voice-transcript ${hasCorrection ? 'corrected' : 'pending'}">
            <span>校验结果</span>
            <p>${escapeHtml(correctionText)}</p>
          </section>
        </div>
      `;
    }

    function closeVoiceComparison() {
      const box = document.getElementById('voiceCompare');
      if (!box) return;
      box.hidden = true;
      box.innerHTML = '';
    }

    function blobToDataUri(blob) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('读取录音失败。'));
        reader.readAsDataURL(blob);
      });
    }

    function stopVoiceTracks() {
      if (!voiceStream) return;
      voiceStream.getTracks().forEach(track => track.stop());
      voiceStream = null;
    }

    function setVoiceButton(state, text) {
      const button = document.getElementById('voiceButton');
      if (!button) return;
      button.textContent = text;
      button.classList.toggle('recording', state === 'recording');
      button.classList.toggle('busy', state === 'busy');
      button.disabled = state === 'busy';
    }

    function setVoiceCorrectionButton(isBusy) {
      const button = document.getElementById('voiceCorrectButton');
      if (!button) return;
      button.disabled = Boolean(isBusy);
      button.textContent = isBusy ? '校验中' : (lastVoiceCorrectedText ? '重新校验' : '校验文本');
    }

    window.toggleVoiceInput = toggleVoiceInput;
    window.correctVoiceText = correctVoiceText;
    window.closeVoiceComparison = closeVoiceComparison;
