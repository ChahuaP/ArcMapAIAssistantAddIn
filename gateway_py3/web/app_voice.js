    let voiceRecorder = null;
    let voiceStream = null;
    let voiceChunks = [];
    let voiceBusy = false;

    async function toggleVoiceInput() {
      if (voiceBusy) return;
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
      setStatus('正在识别语音并校正指令...');
      try {
        const blob = new Blob(voiceChunks, {type: mimeType});
        const audioDataUri = await blobToDataUri(blob);
        const data = await api('/voice/transcribe', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            audio_data_uri: audioDataUri,
            mode: currentMode,
            context: latestArcgisContext || {}
          })
        });
        applyVoiceText(data.text || '');
        renderVoiceComparison(data.raw_text || '', data.text || '');
        setStatus(data.raw_text && data.raw_text !== data.text ? '语音已识别并校正。' : '语音已识别。');
      } catch (err) {
        setStatus(err.message);
      } finally {
        voiceBusy = false;
        voiceChunks = [];
        setVoiceButton('', '语音');
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
      box.innerHTML = `
        <div class="voice-compare-head">
          <span>语音校正</span>
          <button type="button" onclick="closeVoiceComparison()" aria-label="关闭语音校正结果">×</button>
        </div>
        <p><strong>原识别文本：</strong>${escapeHtml(raw || '无')}</p>
        <p><strong>校正后文本：</strong>${escapeHtml(corrected || '无')}</p>
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

    window.toggleVoiceInput = toggleVoiceInput;
    window.closeVoiceComparison = closeVoiceComparison;
