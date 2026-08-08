    const EXPECTED_GATEWAY_VERSION = '2.0.0';
    const API_ORIGIN = window.location.protocol === 'file:' ? 'http://127.0.0.1:8765' : '';
    
    const SESSION_STORAGE_KEY = 'geopilot.sessionId';
    let eventSource = null;
    let eventRefreshBusy = false;
    let eventRefreshTimer = 0;
    let pendingEventTypes = new Set();
    let capabilitiesLoaded = false;
    
    
    let arcmapBridges = [];
    let cachedRuns = [];
    let selectedRunId = '';
    let transientUserMessage = '';
    let transientAssistantMessage = '';
    let modelWait = null;
    let modelWaitTimer = null;
    let providerOptions = [];
    let modelOptions = [];
    let pendingProviderKeyClears = {};
    let pendingApprovalRunId = '';
    const appState = {
      config: null,
      health: null,
      runs: [],
      arcmapBridges: []
    };
    const taskDetailsState = new Map();

    // §5 session isolation: one stable UUID per browser, sent as X-Session-Id.
    // New task = same session (kernel creates a fresh run with its own run_id);
    // clearing history starts a new session.
    function getSessionId() {
      let id = localStorage.getItem(SESSION_STORAGE_KEY);
      if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(SESSION_STORAGE_KEY, id);
      }
      return id;
    }

    function resetSessionId() {
      localStorage.removeItem(SESSION_STORAGE_KEY);
    }

    // §14.4: run stage → user-facing label (no fake timer).
    const STAGE_LABELS = {
      received: '已接收',
      context_frozen: '正在捕获地图上下文',
      intent_compiled: '正在理解任务意图',
      plan_verified: '正在验证执行计划',
      authorization_required: '等待授权确认',
      authorized: '已授权',
      runtime_acquired: '正在绑定 ArcMap',
      executing: '正在执行到 ArcMap',
      executed: '执行完成，正在验收',
      accepted: '验收通过，正在发布',
      published: '已发布',
      succeeded: '任务完成',
      clarification_required: '需要补充信息',
      policy_denied: '授权被拒绝',
      contract_failed: '任务合同校验失败',
      capability_failed: '能力执行失败',
      infrastructure_failed: '基础设施故障',
      quota_stopped: '模型额度不足',
      model_call_uncertain: '模型调用结果不确定',
      execution_indeterminate: '执行状态不确定',
      acceptance_failed: '成果验收失败',
      cancelled: '已取消',
    };

    function stageLabel(stage) {
      return STAGE_LABELS[stage] || stage;
    }

    function isTerminalStage(stage) {
      return stage === 'succeeded' ||
        stage === 'clarification_required' ||
        stage === 'policy_denied' ||
        stage === 'contract_failed' ||
        stage === 'capability_failed' ||
        stage === 'infrastructure_failed' ||
        stage === 'quota_stopped' ||
        stage === 'model_call_uncertain' ||
        stage === 'execution_indeterminate' ||
        stage === 'acceptance_failed' ||
        stage === 'cancelled';
    }

    function isApprovalStage(stage) {
      return stage === 'authorization_required';
    }

    function setState(patch) {
      patch = patch || {};
      Object.assign(appState, patch);
      if (Object.prototype.hasOwnProperty.call(patch, 'runs')) cachedRuns = patch.runs || [];
      if (Object.prototype.hasOwnProperty.call(patch, 'arcmapBridges')) arcmapBridges = patch.arcmapBridges || [];
      
    }

    function renderApp(changedKeys) {
      const keys = new Set(changedKeys || []);
      if (keys.has('runs')) {
        pruneTaskDetailsState(cachedRuns);
        ensureSelectedRun();
        renderTasks(cachedRuns);
        renderConversation(cachedRuns);
      }
      if (keys.has('arcmap')) renderArcMapBridgeState();
    }

    function offlineMessage() {
      return '本地网关未连接。请回到 ArcGIS 工具栏点击“启动控制台”。页面会自动恢复状态。';
    }

    async function api(path, options) {
      let response;
      const opts = options || {};
      // §8: every request carries the session token
      opts.headers = Object.assign({'X-Session-Id': getSessionId()}, opts.headers || {});
      try {
        response = await fetch(apiUrl(path), opts);
      } catch (err) {
        throw new Error(offlineMessage());
      }
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || response.statusText);
      return data;
    }

    function apiUrl(path) {
      if (/^https?:\/\//i.test(path)) return path;
      return `${API_ORIGIN}${path}`;
    }

    function openModal(id) {
      document.getElementById(id).hidden = false;
    }

    function closeModal(id) {
      document.getElementById(id).hidden = true;
    }

    function closeOnBackdrop(event) {
      if (event.target.classList.contains('overlay')) event.target.hidden = true;
    }

    async function openCapabilities() {
      openModal('capabilitiesModal');
      if (!capabilitiesLoaded) await loadCapabilities();
    }

    async function openDiagnostics() {
      openModal('diagnosticsModal');
      await loadDiagnostics();
    }

    function openChangelog() {
      openModal('changelogModal');
    }

    async function openLogDir() {
      try {
        await api('/open-path', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({target: 'log_dir'})
        });
        setStatus('已打开日志目录。');
      } catch (err) {
        setStatus(err.message);
      }
    }

    async function openConfigFile() {
      try {
        await api('/open-path', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({target: 'config_file'})
        });
        setStatus('已打开配置文件。');
      } catch (err) {
        setStatus(err.message);
      }
    }

    async function openPendingTools() {
      openModal('toolsModal');
      await loadPendingTools();
    }

    async function loadDiagnostics() {
      const box = document.getElementById('diagnosticsList');
      box.innerHTML = '<div class="empty-state-card">正在检查...</div>';
      try {
        const data = await api('/api/diagnostics');
      document.getElementById('diagnosticsSummary').textContent = data.ok
        ? `检查通过，当前版本 ${data.app_version}。`
          : `发现需要处理的事项，当前版本 ${data.app_version}。`;
        renderDiagnostics(data.checks || []);
      } catch (err) {
        document.getElementById('diagnosticsSummary').textContent = '诊断失败。';
        box.innerHTML = `<div class="empty-state-card">${escapeHtml(err.message)}</div>`;
      }
    }

    function renderDiagnostics(checks) {
      const labels = {ok: '正常', warn: '提醒', bad: '异常'};
      const box = document.getElementById('diagnosticsList');
      if (!checks.length) {
        box.innerHTML = '<div class="empty-state-card">没有诊断结果。</div>';
        return;
      }
      box.innerHTML = '';
      checks.forEach(item => {
        const status = item.status || 'warn';
        const card = document.createElement('div');
        card.className = `diag-card ${status}`;
        card.innerHTML = `
          <span class="diag-badge">${escapeHtml(labels[status] || status)}</span>
          <div class="diag-body">
            <strong>${escapeHtml(item.label || item.id || '')}</strong>
            <p>${escapeHtml(item.detail || '')}</p>
            ${item.path ? `<code>${escapeHtml(item.path)}</code>` : ''}
          </div>
        `;
        box.appendChild(card);
      });
    }

    async function loadCapabilities() {
      const box = document.getElementById('capabilityGroups');
      try {
        const data = await api('/api/capabilities?detail=1');
        capabilitiesLoaded = true;
        document.getElementById('capabilitiesSummary').textContent =
          `应用版本 ${data.app_version || EXPECTED_GATEWAY_VERSION}，${data.operation_count} 个能力。`;
        renderCapabilities(data.operations || []);
      } catch (err) {
        box.innerHTML = `<div class="empty-state-card">${escapeHtml(err.message)}</div>`;
      }
    }

    function renderCapabilities(operations) {
      const box = document.getElementById('capabilityGroups');
      const groups = {};
      operations.forEach(operation => {
        const category = operation.category || 'other';
        if (!groups[category]) groups[category] = [];
        groups[category].push(operation);
      });
      const order = ['map_context', 'view_layer', 'selection', 'analysis', 'table', 'basemap', 'export', 'other'];
      box.innerHTML = '';
      order.filter(category => groups[category]).forEach(category => {
        const group = document.createElement('section');
        group.className = 'capability-group';
        group.innerHTML = `<h3>${escapeHtml(categoryLabel(category))}</h3>`;
        const list = document.createElement('div');
        list.className = 'capability-list';
        groups[category].forEach(operation => {
          const card = document.createElement('div');
          card.className = 'capability-card';
          card.innerHTML = `
            <strong>${escapeHtml(operationTitle(operation))}</strong>
            <p>${escapeHtml(operation.summary || '')}</p>
            ${operation.example ? `<p>例如：${escapeHtml(operation.example)}</p>` : ''}
            <code>${escapeHtml(operation.id)}</code>
          `;
          list.appendChild(card);
        });
        group.appendChild(list);
        box.appendChild(group);
      });
    }

    function categoryLabel(category) {
      return {
        map_context: '地图上下文',
        view_layer: '视图与图层',
        selection: '选择',
        analysis: '常用分析',
        table: '属性表',
        basemap: '底图',
        export: '导出',
        other: '其他'
      }[category] || category;
    }

    function operationTitle(operation) {
      const parts = String(operation.id || '').split('.');
      return parts[parts.length - 1].replace(/_/g, ' ');
    }

    function setStatus(text) {
      document.getElementById('status').textContent = text;
    }

    function startModelWait(label, stage) {
      stopModelWait();
      modelWait = {label, startedAt: Date.now(), stage: stage || 'received'};
      updateModelWait();
      modelWaitTimer = window.setInterval(updateModelWait, 1000);
      const button = document.getElementById('sendButton');
      if (button) button.disabled = true;
    }

    function stopModelWait() {
      if (modelWaitTimer) {
        window.clearInterval(modelWaitTimer);
        modelWaitTimer = null;
      }
      modelWait = null;
      const button = document.getElementById('sendButton');
      if (button) button.disabled = false;
    }

    function setModelWaitStage(stage) {
      if (!modelWait) return;
      modelWait.stage = stage;
      updateModelWait();
    }

    function updateModelWait() {
      if (!modelWait) return;
      setStatus(`${modelWait.label}：${stageLabel(modelWait.stage)}（已等待 ${formatDuration(modelWaitElapsed())}）`);
      const bubble = document.getElementById('modelWaitBubble');
      if (bubble) {
        bubble.innerHTML = renderModelWait();
      } else if (transientUserMessage) {
        renderConversation(cachedRuns);
      }
    }

    function modelWaitElapsed() {
      return Math.max(0, Math.floor((Date.now() - modelWait.startedAt) / 1000));
    }

    function formatDuration(totalSeconds) {
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    }

    function modelWaitStageIndex() {
      const order = ['received', 'context_frozen', 'intent_compiled', 'plan_verified', 'authorized', 'runtime_acquired', 'executing', 'executed', 'accepted', 'published', 'succeeded'];
      const stage = (modelWait && modelWait.stage) || 'received';
      const index = order.indexOf(stage);
      return index >= 0 ? index : 0;
    }

    function renderModelWait() {
      const stages = ['接收', '上下文', '意图', '计划', '授权', '绑定', '执行', '验收', '发布', '完成'];
      const active = Math.min(modelWaitStageIndex(), stages.length - 1);
      return `
        <div class="model-wait" aria-live="polite">
          <div class="model-wait-header">
            <strong class="model-wait-title">${escapeHtml(modelWait.label)}</strong>
            <span class="model-wait-time">${formatDuration(modelWaitElapsed())}</span>
          </div>
          <div class="model-wait-steps">
            ${stages.map((stage, index) => `<span class="model-wait-step ${index === active ? 'active' : index < active ? 'done' : ''}">${stage}</span>`).join('')}
          </div>
          <div class="model-wait-note">${escapeHtml(stageLabel(modelWait.stage))}</div>
        </div>
      `;
    }

    function setTile(id, state, text) {
      const tile = document.getElementById(id);
      tile.className = `status-item ${state}`;
      tile.querySelector('.status-val').textContent = text;
    }

    function setDot(id, ok, warn) {
      const dot = document.getElementById(id);
      dot.className = 'config-dot' + (ok ? ' ok' : warn ? ' warn' : '');
    }

    async function openHealth(options) {
      const silent = options && options.silent;
      const data = await api('/health');
      applyHealthData(data, silent);
    }

    function applyHealthData(data, silent) {
      setState({health: data || null});
      const version = data.app_version || '旧版本';
      setTile('gatewayState', 'ok', `已启动，${data.operation_count} 个能力`);
      const versionNode = document.getElementById('versionInfo');
      if (versionNode) versionNode.textContent = version;
      if (data.app_version === EXPECTED_GATEWAY_VERSION) {
        updateModeStatus();
      } else {
        setTile('restartState', 'warn', '需要重启网关');
      }
      if (!silent) setStatus(`网关已连接，版本 ${version}。`);
    }

    async function loadWorkbenchState() {
      const data = await api('/api/workbench-state');
      try {
        applyHealthData(data.health || {}, true);
        applyConfig(data.config || {});
        applyArcMapBridges((data.arcmap && data.arcmap.bridges) || [], (data.arcmap && data.arcmap.error) || '');
        applyRuns(data.runs || [], true);
        setStatus(`网关已连接，版本 ${(data.health && data.health.app_version) || EXPECTED_GATEWAY_VERSION}。`);
      } catch (renderErr) {
        console.error('loadWorkbenchState render error:', renderErr);
        // Still set health so the UI shows connected
        applyHealthData(data.health || {}, true);
      }
    }

    async function loadConfig() {
      const data = await api('/config');
      applyConfig(data.config);
    }

    async function saveConfig() {
      const primaryModel = parseModelChoice(document.getElementById('primaryProvider').value);
      const data = await api('/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          primary_provider: primaryModel.provider,
          primary_model: primaryModel.model,
          providers: collectProviderConfig()
        })
      });
      pendingProviderKeyClears = {};
      applyConfig(data.config);
      setStatus('模型配置已保存。');
      closeModal('keyModal');
    }

    function applyConfig(config) {
      const providers = config.providers || {};
      setState({config});
      renderModelConfig(config);
      const keyStates = providerKeyStates(providers);
      const ok = keyStates.some(item => item.ok);
      updateModeUI();
      document.getElementById('keyBadge').textContent = providerKeyLabel(keyStates);
      document.getElementById('keyActionText').textContent = '模型配置';
      document.getElementById('configPathHint').textContent = config.config_error
        ? `配置文件：${config.config_path || '未知'}。旧配置无效，请重新保存模型配置：${config.config_error}`
        : `配置文件：${config.config_path || '未知'}`;
      renderSpeechConfigHint(config);
      renderCurrentModelHint(config);
      setDot('keyDot', ok, !ok);
    }

    function renderModelConfig(config) {
      providerOptions = providerList(config);
      modelOptions = Array.isArray(config.model_options) ? config.model_options : [];
      renderModelSelect('primaryProvider', config.primary_provider, config.primary_model);
      renderProviderKeyFields(config.providers || {});
      renderCurrentModelHint(config);
    }

    function renderCurrentModelHint(config) {
      const node = document.getElementById('activeModelHint');
      if (!node || !config) return;
      const primary = modelOptionLabel(config.primary_provider, config.primary_model);
      node.textContent = `当前模型：${primary}`;
    }

    function renderSpeechConfigHint(config) {
      const speech = config.speech || {};
      const provider = providerLabel(speech.uses_provider || 'qwen');
      const keyState = speech.has_api_key ? '已可用' : '未配置';
      const sourceLabel = speech.api_key_source && speech.api_key_source.label ? `，运行时使用：${speech.api_key_source.label}` : '';
      document.getElementById('speechConfigHint').textContent =
        `语音识别使用 ${provider} API Key 或 Token Plan API Key，模型 ${speech.model || 'qwen3-asr-flash'}，当前${keyState}${sourceLabel}。`;
    }

    function providerList(config) {
      if (Array.isArray(config.provider_options) && config.provider_options.length) {
        return config.provider_options.map(item => ({
          id: String(item.id || '').trim(),
          label: String(item.label || item.id || '').trim(),
          env_key: String(item.env_key || '').trim(),
          env_keys: Array.isArray(item.env_keys) ? item.env_keys.map(value => String(value || '').trim()).filter(Boolean) : [],
          key_placeholder: String(item.key_placeholder || 'API Key').trim(),
          key_fields: providerKeyFields(item)
        })).filter(item => item.id);
      }
      return Object.entries(config.providers || {}).map(([id, item]) => ({
        id,
        label: String((item && item.label) || id),
        env_key: String((item && item.env_key) || ''),
        env_keys: [],
        key_placeholder: 'API Key',
        key_fields: [{field: 'api_key', label: 'API Key', placeholder: 'API Key'}]
      }));
    }

    function renderModelSelect(id, provider, model) {
      const select = document.getElementById(id);
      select.innerHTML = '';
      providerOptions.forEach(providerOption => {
        const options = modelOptions.filter(option => option.provider === providerOption.id);
        if (!options.length) return;
        const group = document.createElement('optgroup');
        group.label = providerOption.label || providerOption.id;
        options.forEach(option => {
          const node = document.createElement('option');
          node.value = modelChoiceValue(option.provider, modelOptionId(option));
          node.textContent = option.label || modelOptionId(option);
          group.appendChild(node);
        });
        select.appendChild(group);
      });
      select.value = selectedModelValue(provider, model);
    }

    function renderProviderKeyFields(providers) {
      const box = document.getElementById('providerKeyFields');
      box.innerHTML = '';
      providerOptions.forEach(provider => {
        const settings = providers[provider.id] || {};
        const card = document.createElement('section');
        card.className = 'provider-card';
        card.innerHTML = `
          <div class="provider-card-header">
            <strong>${escapeHtml(provider.label)}</strong>
            <span>${settings.has_api_key ? '可用' : '未配置'}</span>
          </div>
          ${provider.key_fields.map(field => providerKeyFieldHtml(provider, field, settings)).join('')}
          <label for="${providerBaseUrlId(provider.id)}">接口地址</label>
          <input id="${providerBaseUrlId(provider.id)}" type="text" value="${escapeHtml(settings.base_url || '')}" autocomplete="off">
          <p class="form-hint">运行时使用：${escapeHtml(providerRuntimeKeyLabel(settings))}</p>
          ${providerEnvKeys(provider).length ? `<p class="form-hint">也可使用环境变量 ${escapeHtml(providerEnvKeys(provider).join('、'))}。</p>` : ''}
        `;
        box.appendChild(card);
      });
    }

    function providerKeyFields(provider) {
      const fields = Array.isArray(provider.key_fields) ? provider.key_fields : [];
      const normalized = fields.map(field => ({
        field: String(field.field || '').trim(),
        label: String(field.label || field.field || '').trim(),
        placeholder: String(field.placeholder || field.label || 'API Key').trim()
      })).filter(field => field.field);
      return normalized.length ? normalized : [{field: 'api_key', label: 'API Key', placeholder: provider.key_placeholder || 'API Key'}];
    }

    function providerKeyFieldHtml(provider, field, settings) {
      const keyStatus = settings.key_status || {};
      const saved = keyStatus[field.field] ? '已保存' : '未配置';
      const hasSavedKey = Boolean(keyStatus[field.field]);
      return `
        <label for="${providerInputId(provider.id, field.field)}">${escapeHtml(field.label)} <span id="${providerKeyStatusId(provider.id, field.field)}" class="form-hint">${escapeHtml(saved)}</span></label>
        <div class="provider-key-row">
          <input id="${providerInputId(provider.id, field.field)}" type="password" placeholder="${escapeHtml(field.placeholder)}" autocomplete="off" oninput="handleProviderKeyInput('${escapeJs(provider.id)}', '${escapeJs(field.field)}')">
          <button id="${providerClearButtonId(provider.id, field.field)}" type="button" class="btn btn-danger btn-sm" data-saved="${hasSavedKey ? '1' : '0'}" ${hasSavedKey ? '' : 'disabled'} onclick="markProviderKeyForClear('${escapeJs(provider.id)}', '${escapeJs(field.field)}', '${escapeJs(field.label)}')">清除</button>
        </div>
      `;
    }

    function markProviderKeyForClear(providerId, field, label) {
      if (!window.confirm(`确定清除 ${providerLabel(providerId)} 的 ${label} 吗？`)) return;
      const fields = pendingProviderKeyClears[providerId] || [];
      if (!fields.includes(field)) fields.push(field);
      pendingProviderKeyClears[providerId] = fields;
      const input = document.getElementById(providerInputId(providerId, field));
      if (input) {
        input.value = '';
        input.placeholder = '保存后清除';
      }
      const status = document.getElementById(providerKeyStatusId(providerId, field));
      if (status) status.textContent = '保存后清除';
      const button = document.getElementById(providerClearButtonId(providerId, field));
      if (button) {
        button.textContent = '待清除';
        button.disabled = true;
      }
      setStatus('保存模型配置后会清除这个 Key。');
    }

    function handleProviderKeyInput(providerId, field) {
      const fields = pendingProviderKeyClears[providerId] || [];
      pendingProviderKeyClears[providerId] = fields.filter(item => item !== field);
      if (!pendingProviderKeyClears[providerId].length) delete pendingProviderKeyClears[providerId];
      const input = document.getElementById(providerInputId(providerId, field));
      const button = document.getElementById(providerClearButtonId(providerId, field));
      const status = document.getElementById(providerKeyStatusId(providerId, field));
      if (button) {
        button.textContent = '清除';
        button.disabled = button.dataset.saved !== '1';
      }
      if (status) {
        status.textContent = input && input.value.trim()
          ? '待保存'
          : ((button && button.dataset.saved === '1') ? '已保存' : '未配置');
      }
    }

    function providerRuntimeKeyLabel(settings) {
      return (settings.api_key_source && settings.api_key_source.label) || (settings.has_api_key ? '已配置 Key' : '未配置');
    }

    function providerEnvKeys(provider) {
      if (Array.isArray(provider.env_keys) && provider.env_keys.length) return provider.env_keys;
      return provider.env_key ? [provider.env_key] : [];
    }

    function collectProviderConfig() {
      const providers = {};
      providerOptions.forEach(provider => {
        const baseUrlInput = document.getElementById(providerBaseUrlId(provider.id));
        const item = {};
        provider.key_fields.forEach(field => {
          const input = document.getElementById(providerInputId(provider.id, field.field));
          if (input && input.value.trim()) item[field.field] = input.value.trim();
        });
        const clearSecretFields = pendingProviderKeyClears[provider.id] || [];
        if (clearSecretFields.length) item.clear_secret_fields = clearSecretFields.slice();
        if (baseUrlInput && baseUrlInput.value.trim()) item.base_url = baseUrlInput.value.trim();
        if (Object.keys(item).length) providers[provider.id] = item;
      });
      return providers;
    }

    function providerKeyStatusId(providerId, field) {
      return `providerKeyStatus_${providerId}_${field || 'api_key'}`;
    }

    function providerClearButtonId(providerId, field) {
      return `providerKeyClear_${providerId}_${field || 'api_key'}`;
    }

    function providerInputId(providerId, field) {
      return `providerKey_${providerId}_${field || 'api_key'}`;
    }

    function providerBaseUrlId(providerId) {
      return `providerBaseUrl_${providerId}`;
    }

    function providerLabel(providerId) {
      const provider = providerOptions.find(item => item.id === providerId);
      return provider ? provider.label : providerId;
    }

    function parseModelChoice(value) {
      const parts = String(value || '').split('|');
      const first = firstModelOption();
      return {provider: parts[0] || first.provider, model: parts[1] || modelOptionId(first)};
    }

    function modelChoiceValue(provider, model) {
      const first = firstModelOption();
      return `${provider || first.provider}|${model || modelOptionId(first)}`;
    }

    function selectedModelValue(provider, model) {
      const value = modelChoiceValue(provider, model);
      const known = new Set(modelOptions.map(option => modelChoiceValue(option.provider, modelOptionId(option))));
      if (known.has(value)) return value;
      const first = firstModelOption();
      return modelChoiceValue(first.provider, modelOptionId(first));
    }

    function firstModelOption() {
      return modelOptions[0] || {provider: 'deepseek', id: 'deepseek-v4-flash-thinking', model: 'deepseek-v4-flash-thinking'};
    }

    function modelOptionId(option) {
      return String((option && (option.id || option.model)) || '').trim();
    }

    function modelOptionLabel(provider, model) {
      const option = modelOptions.find(item => item.provider === provider && modelOptionId(item) === model);
      if (option) return option.label || modelOptionId(option);
      return `${providerLabel(provider)} ${model || ''}`.trim();
    }

    function providerKeyStates(providers) {
      return providerOptions.map(provider => ({
        label: provider.label,
        ok: providers[provider.id] && providers[provider.id].has_api_key
      }));
    }

    function providerKeyLabel(states) {
      const saved = states.filter(item => item.ok).map(item => item.label);
      if (saved.length > 1) return `${saved.join('、')} 已保存`;
      if (saved.length === 1) return `${saved[0]} 已保存`;
      return 'Key 未配置';
    }

    async function setMode(mode) {
      // Mode system removed in 2.0 — kept as no-op for backwards compatibility
      // with any lingering HTML onclick references.
    }

    function updateModeUI() {
      renderCurrentModelHint(appState.config);
      renderTasks(cachedRuns);
      renderConversation(cachedRuns);
    }

    async function loadArcMapBridges() {
      try {
        const data = await api('/arcmap/bridges');
        applyArcMapBridges(data.bridges || []);
      } catch (err) {
        applyArcMapBridges([], err.message);
      }
    }

    function applyArcMapBridges(bridges, error) {
      setState({arcmapBridges: bridges || []});
      renderArcMapBridgeState(error || '');
    }

    function renderArcMapBridgeState(error) {
      if (error || !arcmapBridges.length) {
        setTile('arcgisState', 'bad', '未连接');
        return;
      }
      setTile('arcgisState', 'ok', '已连接');
    }

    function activeArcMapBridge() {
      return arcmapBridges.find(item => item.active) || arcmapBridges.find(item => item.hwnd) || arcmapBridges[0] || null;
    }

    function arcmapBridgeLabel(bridge, count) {
      if (!bridge) return '未连接';
      return '已连接';
    }

    function updateModeStatus() {
    }

    function taskScopeText() {
      return '输入 GIS 指令';
    }

    async function loadPendingTools() {
      const data = await api('/tools/pending');
      renderPendingTools(data.tools || []);
    }

    function renderPendingTools(tools) {
      const box = document.getElementById('pendingToolsList');
      if (!tools.length) {
        box.innerHTML = '<div class="empty-state-card">暂无自定义工具。</div>';
        return;
      }
      box.innerHTML = '';
      tools.forEach(tool => {
        const statusText = {pending_review: '待审核', enabled: '已启用', rejected: '已拒绝'}[tool.status] || tool.status;
        const card = document.createElement('div');
        card.className = 'tool-item';
        const revision = ((tool.payload || {}).revision || {}).number || 1;
        card.innerHTML = `
          <strong>${escapeHtml(tool.name)} · ${escapeHtml(statusText)}</strong>
          <p>${escapeHtml(tool.capability)}</p>
          <p class="form-hint">${escapeHtml((tool.payload.operation_spec || {}).id || '')} · rev ${escapeHtml(revision)}</p>
        `;
        if (tool.status === 'pending_review') {
          const actions = document.createElement('div');
          actions.className = 'tool-actions';
          actions.innerHTML = `
            <button class="btn btn-success btn-sm" onclick="enableTool('${escapeJs(tool.id)}')">启用</button>
            <button class="btn btn-danger-outline btn-sm" onclick="rejectTool('${escapeJs(tool.id)}')">拒绝</button>
            <button class="btn btn-danger btn-sm" onclick="deleteTool('${escapeJs(tool.id)}')">删除</button>
          `;
          card.appendChild(actions);
        } else {
          const actions = document.createElement('div');
          actions.className = 'tool-actions';
          actions.innerHTML = `<button class="btn btn-danger btn-sm" onclick="deleteTool('${escapeJs(tool.id)}')">删除</button>`;
          card.appendChild(actions);
        }
        box.appendChild(card);
      });
    }

    async function enableTool(id) {
      await api(`/tools/${id}/enable`, {method: 'POST', body: '{}'});
      capabilitiesLoaded = false;
      setStatus('工具已启用，后续规划可以使用。');
      await loadPendingTools();
    }

    async function rejectTool(id) {
      await api(`/tools/${id}/reject`, {method: 'POST', body: '{}'});
      setStatus('已拒绝该工具。');
      await loadPendingTools();
    }

    async function deleteTool(id) {
      if (!window.confirm('确定删除这个自建工具吗？删除后它会从能力范围里移除。')) return;
      await api(`/tools/${id}/delete`, {method: 'POST', body: '{}'});
      capabilitiesLoaded = false;
      setStatus('自建工具已删除。');
      await loadPendingTools();
    }

    async function submitPlan() {
      if (modelWait) return;
      const input = document.getElementById('command');
      const command = input.value.trim();
      if (!command) return;
      input.value = '';
      transientUserMessage = command;
      transientAssistantMessage = '';
      const execute = true;
        startModelWait('模型正在思考', 'received');
        selectedRunId = '';
        renderConversation(cachedRuns);
      try {
        setStatus('正在提交任务...');
        const payload = {text: command, execute: execute};
        // side_effect_level is not declared by the caller — the plan's
        // risk_level determines if authorization is needed (level >= 2).
        const data = await api('/api/v1/runs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        // Keep transientUserMessage alive so the modelWait bubble persists.
        // It will be cleared when the run reaches terminal state in
        // handleRunStageChanged -> stopModelWait -> refreshRuns.
        transientAssistantMessage = '';
        const run = data.run;
        selectedRunId = run.run_id;
        setModelWaitStage(run.stage);
        renderConversation(cachedRuns);
        // §14: SSE drives the wait. No polling.
        await waitForRunSSE(run.run_id, run.stage);
      } catch (err) {
        stopModelWait();
        transientAssistantMessage = err.message;
        renderConversation(cachedRuns);
        setStatus(err.message);
      }
    }

    // §14: SSE-driven run wait. Replaces the old 250ms-2000ms backoff poll.
    // The SSE stream pushes run.stage_changed events; we react to them.
    async function waitForRunSSE(runId, initialStage) {
      // One immediate inspect to sync the current stage (the background
      // thread may have already advanced past 'received'). After this,
      // SSE events drive all subsequent updates — no polling.
      try {
        const data = await api(`/api/v1/runs/${runId}`);
        handleRunStageChanged(runId, data.run.stage);
        if (isTerminalStage(data.run.stage) || isApprovalStage(data.run.stage)) return;
      } catch (err) { /* SSE will handle it */ }
    }

    // Called by the SSE handler when a run.stage_changed event arrives.
    async function handleRunStageChanged(runId, stage) {
      if (selectedRunId !== runId) return;
      setModelWaitStage(stage);
      if (isApprovalStage(stage)) {
        pendingApprovalRunId = runId;
        renderApprovalPrompt(runId);
        return;
      }
      if (isTerminalStage(stage)) {
        stopModelWait();
        transientUserMessage = '';
        await refreshRuns();
        setStatus(stageLabel(stage));
      }
    }

    // §6.6 authorization confirmation: the user must approve before execution.
    window.approveRun = async function(runId) {
      pendingApprovalRunId = '';
      removeApprovalPrompt();
      startModelWait('正在执行', 'authorized');
      try {
        const data = await api(`/api/v1/runs/${runId}/decide`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({approved: true})
        });
        setModelWaitStage(data.run.stage);
        if (isTerminalStage(data.run.stage)) {
          stopModelWait();
          await refreshRuns();
          setModelWaitStage(data.run.stage);
        }
      } catch (err) {
        setStatus(err.message);
      }
    }

    window.denyRun = async function(runId) {
      pendingApprovalRunId = '';
      removeApprovalPrompt();
      try {
        await api(`/api/v1/runs/${runId}/decide`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({approved: false})
        });
        await refreshRuns();
        setStatus('已拒绝授权。');
      } catch (err) {
        setStatus(err.message);
      }
    }

    function renderApprovalPrompt(runId) {
      const chat = document.getElementById('chatLog');
      const existing = document.getElementById('approvalPrompt');
      if (existing) existing.remove();
      const prompt = document.createElement('div');
      prompt.id = 'approvalPrompt';
      prompt.className = 'approval-prompt';
      prompt.innerHTML = `
        <div class="approval-body">
          <p class="approval-title">⚠️ 执行授权确认</p>
          <p>该任务将对 ArcMap 地图产生操作。是否确认执行？</p>
          <div class="approval-actions">
            <button class="btn-primary" onclick="approveRun('${escapeJs(runId)}')">确认执行</button>
            <button class="btn-secondary" onclick="denyRun('${escapeJs(runId)}')">拒绝</button>
          </div>
        </div>
      `;
      chat.appendChild(prompt);
      chat.scrollTop = chat.scrollHeight;
    }

    function removeApprovalPrompt() {
      const prompt = document.getElementById('approvalPrompt');
      if (prompt) prompt.remove();
    }

    async function clearConversation() {
      resetSessionId();
      selectedRunId = '';
      transientUserMessage = '';
      transientAssistantMessage = '';
      stopModelWait();
      setStatus('已清空会话。');
      cachedRuns = [];
      renderConversation(cachedRuns);
      renderTasks(cachedRuns);
    }

    async function deleteRun(id) {
      try {
        await api(`/api/v1/runs/${id}/delete`, {method: 'POST', body: '{}'});
      } catch (err) { /* ignore */ }
      if (selectedRunId === id) selectedRunId = '';
      cachedRuns = cachedRuns.filter(r => r.run_id !== id && r.id !== id);
      renderTasks(cachedRuns);
      renderConversation(cachedRuns);
      setStatus('已删除。');
    }

    async function refreshRuns(renderChat = true) {
      const data = await api('/api/v1/runs');
      applyRuns(data.runs || [], renderChat);
    }

    function runListPath() {
      return '/api/v1/runs';
    }

    function applyRuns(runs, renderChat = true) {
      setState({runs: runs || []});
      pruneTaskDetailsState(cachedRuns);
      ensureSelectedRun();
      renderTasks(cachedRuns);
      if (renderChat) renderConversation(cachedRuns);
    }
