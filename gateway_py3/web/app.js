    const EXPECTED_GATEWAY_VERSION = '0.19.0';
    const API_ORIGIN = window.location.protocol === 'file:' ? 'http://127.0.0.1:8765' : '';
    const MODE_STORAGE_KEY = 'geopilot.currentMode';
    let eventSource = null;
    let eventRefreshBusy = false;
    let eventRefreshTimer = 0;
    let pendingEventTypes = new Set();
    let capabilitiesLoaded = false;
    let currentMode = 'semi_agent';
    let modeInitialized = false;
    let activeProject = null;
    let latestArcgisContext = null;
    let arcmapBridges = [];
    let cachedWorkflows = [];
    let selectedWorkflowId = '';
    let transientUserMessage = '';
    let transientAssistantMessage = '';
    let modelWait = null;
    let modelWaitTimer = null;
    let repairingWorkflowIds = new Set();
    let providerOptions = [];
    let modelOptions = [];
    const appState = {
      config: null,
      health: null,
      projects: [],
      activeProject: null,
      context: null,
      workflows: [],
      arcmapBridges: [],
      agentProgress: null
    };
    const taskDetailsState = new Map();
    let mentionState = null;

    function setState(patch) {
      patch = patch || {};
      Object.assign(appState, patch);
      if (Object.prototype.hasOwnProperty.call(patch, 'activeProject')) activeProject = patch.activeProject || null;
      if (Object.prototype.hasOwnProperty.call(patch, 'context')) latestArcgisContext = patch.context || null;
      if (Object.prototype.hasOwnProperty.call(patch, 'workflows')) cachedWorkflows = patch.workflows || [];
      if (Object.prototype.hasOwnProperty.call(patch, 'arcmapBridges')) arcmapBridges = patch.arcmapBridges || [];
      if (Object.prototype.hasOwnProperty.call(patch, 'currentMode')) currentMode = patch.currentMode || currentMode;
    }

    function renderApp(changedKeys) {
      const keys = new Set(changedKeys || []);
      if (keys.has('projects')) {
        renderProjects(appState.projects || []);
        updateProjectStatus();
      }
      if (keys.has('workflows')) {
        pruneTaskDetailsState(cachedWorkflows);
        ensureSelectedWorkflow();
        renderTasks(cachedWorkflows);
        renderSidebarItems(cachedWorkflows);
        renderConversation(cachedWorkflows);
      }
      if (keys.has('arcmap')) renderArcMapBridgeState();
    }

    function offlineMessage() {
      return '本地网关未连接。请回到 ArcGIS 工具栏点击“启动网关”或“打开助手”。页面会自动恢复状态。';
    }

    async function api(path, options) {
      let response;
      try {
        response = await fetch(apiUrl(path), options || {});
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
      if (event.target.classList.contains('modal')) event.target.hidden = true;
    }

    async function openCapabilities() {
      openModal('capabilitiesModal');
      if (!capabilitiesLoaded) await loadCapabilities();
    }

    async function openDiagnostics() {
      openModal('diagnosticsModal');
      await loadDiagnostics();
    }

    async function openPendingTools() {
      openModal('toolsModal');
      await loadPendingTools();
    }

    async function loadDiagnostics() {
      const box = document.getElementById('diagnosticsList');
      box.innerHTML = '<div class="section-card">正在检查...</div>';
      try {
        const data = await api('/api/diagnostics');
        document.getElementById('diagnosticsSummary').textContent = data.ok
          ? `检查通过，当前版本 ${data.app_version}。`
          : `发现需要处理的项目，当前版本 ${data.app_version}。`;
        renderDiagnostics(data.checks || []);
      } catch (err) {
        document.getElementById('diagnosticsSummary').textContent = '诊断失败。';
        box.innerHTML = `<div class="section-card">${escapeHtml(err.message)}</div>`;
      }
    }

    function renderDiagnostics(checks) {
      const labels = {ok: '正常', warn: '提醒', bad: '异常'};
      const box = document.getElementById('diagnosticsList');
      if (!checks.length) {
        box.innerHTML = '<div class="section-card">没有诊断结果。</div>';
        return;
      }
      box.innerHTML = '';
      checks.forEach(item => {
        const status = item.status || 'warn';
        const card = document.createElement('div');
        card.className = `diagnostic-card ${status}`;
        card.innerHTML = `
          <span class="diagnostic-state">${escapeHtml(labels[status] || status)}</span>
          <div>
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
        box.innerHTML = `<div class="section-card">${escapeHtml(err.message)}</div>`;
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
            ${operation.example ? `<p class="hint">例如：${escapeHtml(operation.example)}</p>` : ''}
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

    function startModelWait(label) {
      stopModelWait();
      modelWait = {label, startedAt: Date.now()};
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

    function updateModelWait() {
      if (!modelWait) return;
      setStatus(`${modelWait.label}，已等待 ${formatDuration(modelWaitElapsed())}`);
      const bubble = document.getElementById('modelWaitBubble');
      if (bubble) {
        bubble.innerHTML = renderModelWait();
      } else if (transientUserMessage) {
        renderConversation(cachedWorkflows);
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
      const order = ['sync_arcmap', 'read_capabilities', 'analyze', 'read_fields', 'model', 'generate_workflow', 'validate', 'execute_arcmap', 'complete'];
      const stage = (modelWait && modelWait.stage) || (appState.agentProgress && appState.agentProgress.stage) || '';
      const index = order.indexOf(stage);
      if (index >= 0) return Math.min(7, index);
      return Math.min(7, Math.floor(modelWaitElapsed() / 12));
    }

    function renderModelWait() {
      const stages = ['同步 ArcMap', '读取能力', '分析任务', '读取字段', '生成 workflow', '校验任务', '执行到 ArcMap', '完成/失败'];
      const notes = [
        '正在确认当前 ArcMap 目标和地图状态。',
        '正在读取可用操作与工具目录。',
        '正在理解任务和项目上下文。',
        '需要时会读取字段和值样本。',
        '正在生成可校验的任务流程。',
        '正在做本地规则校验。',
        '全代理模式会直接发送到 ArcMap。',
        '等待最终结果返回。'
      ];
      const active = modelWaitStageIndex();
      return `
        <div class="model-wait" aria-live="polite">
          <div class="model-wait-head">
            <strong>${escapeHtml(modelWait.label)}</strong>
            <span>${formatDuration(modelWaitElapsed())}</span>
          </div>
          <div class="model-wait-bar" aria-hidden="true"><span></span></div>
          <div class="model-wait-steps">
            ${stages.map((stage, index) => `<span class="${index === active ? 'active' : ''}">${stage}</span>`).join('')}
          </div>
          <div class="model-wait-note">${notes[active]}</div>
        </div>
      `;
    }

    function setTile(id, state, text) {
      const tile = document.getElementById(id);
      tile.className = `status-tile ${state}`;
      tile.querySelector('strong').textContent = text;
    }

    function setDot(id, ok, warn) {
      const dot = document.getElementById(id);
      dot.className = 'dot' + (ok ? ' ok' : warn ? ' warn' : '');
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
      if (data.app_version === EXPECTED_GATEWAY_VERSION) {
        updateProjectStatus();
      } else {
        setTile('restartState', 'warn', '需要重启网关');
      }
      if (!silent) setStatus(`网关已连接，版本 ${version}。`);
    }

    async function loadWorkbenchState() {
      const data = await api('/api/workbench-state');
      applyHealthData(data.health || {}, true);
      applyConfig(data.config || {});
      applyArcMapBridges((data.arcmap && data.arcmap.bridges) || [], (data.arcmap && data.arcmap.error) || '');
      applyContextRecord(data.context || null);
      applyProjects(data.projects || [], data.active_project || null);
      applyWorkflows(data.workflows || [], true);
      setStatus(`网关已连接，版本 ${(data.health && data.health.app_version) || EXPECTED_GATEWAY_VERSION}。`);
    }

    function loadStoredMode() {
      try {
        const mode = localStorage.getItem(MODE_STORAGE_KEY);
        return (mode === 'semi_agent' || mode === 'full_agent') ? mode : '';
      } catch (err) {
        return '';
      }
    }

    function storeMode(mode) {
      try {
        localStorage.setItem(MODE_STORAGE_KEY, mode);
      } catch (err) {
        // 浏览器禁用本地存储时，当前页面状态仍然有效。
      }
    }

    async function loadConfig() {
      const data = await api('/config');
      applyConfig(data.config);
    }

    async function saveConfig() {
      const semiModel = parseModelChoice(document.getElementById('semiProvider').value);
      const fullModel = parseModelChoice(document.getElementById('fullProvider').value);
      const data = await api('/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          default_mode: currentMode,
          semi_agent_provider: semiModel.provider,
          semi_agent_model: semiModel.model,
          full_agent_provider: fullModel.provider,
          full_agent_model: fullModel.model,
          providers: collectProviderConfig()
        })
      });
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
      if (!modeInitialized) {
        currentMode = loadStoredMode() || config.default_mode || currentMode;
        modeInitialized = true;
      }
      updateModeUI();
      document.getElementById('keyBadge').textContent = providerKeyLabel(keyStates);
      document.getElementById('keyActionText').textContent = '模型配置';
      document.getElementById('configPathHint').textContent = `配置文件：${config.config_path || '未知'}`;
      renderSpeechConfigHint(config);
      renderCurrentModelHint(config);
      setDot('keyDot', ok, !ok);
    }

    function renderModelConfig(config) {
      providerOptions = providerList(config);
      modelOptions = Array.isArray(config.model_options) ? config.model_options : [];
      renderModelSelect('semiProvider', config.semi_agent_provider, config.semi_agent_model);
      renderModelSelect('fullProvider', config.full_agent_provider, config.full_agent_model);
      renderProviderKeyFields(config.providers || {});
      renderCurrentModelHint(config);
    }

    function renderCurrentModelHint(config) {
      const node = document.getElementById('activeModelHint');
      if (!node || !config) return;
      const provider = currentMode === 'full_agent' ? config.full_agent_provider : config.semi_agent_provider;
      const model = currentMode === 'full_agent' ? config.full_agent_model : config.semi_agent_model;
      const modeLabel = currentMode === 'full_agent' ? '全代理' : '半代理';
      node.textContent = `${modeLabel}当前使用：${providerLabel(provider)} ${model || ''}`.trim();
    }

    function renderSpeechConfigHint(config) {
      const speech = config.speech || {};
      const provider = providerLabel(speech.uses_provider || 'qwen');
      const keyState = speech.has_api_key ? '已可用' : '未配置';
      document.getElementById('speechConfigHint').textContent =
        `语音识别使用 ${provider} API Key，模型 ${speech.model || 'qwen3-asr-flash'}，当前${keyState}。`;
    }

    function providerList(config) {
      if (Array.isArray(config.provider_options) && config.provider_options.length) {
        return config.provider_options.map(item => ({
          id: String(item.id || '').trim(),
          label: String(item.label || item.id || '').trim(),
          env_key: String(item.env_key || '').trim(),
          key_placeholder: String(item.key_placeholder || 'API Key').trim()
        })).filter(item => item.id);
      }
      return Object.entries(config.providers || {}).map(([id, item]) => ({
        id,
        label: String((item && item.label) || id),
        env_key: String((item && item.env_key) || ''),
        key_placeholder: 'API Key'
      }));
    }

    function renderModelSelect(id, provider, model) {
      const select = document.getElementById(id);
      select.innerHTML = '';
      modelOptions.forEach(option => {
        const node = document.createElement('option');
        node.value = modelChoiceValue(option.provider, option.model);
        node.textContent = option.label || `${providerLabel(option.provider)} ${option.model}`;
        select.appendChild(node);
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
          <div class="provider-card-head">
            <strong>${escapeHtml(provider.label)}</strong>
            <span>${settings.has_api_key ? '已保存' : '未配置'}</span>
          </div>
          <label for="${providerInputId(provider.id)}">API Key</label>
          <input id="${providerInputId(provider.id)}" type="password" placeholder="${escapeHtml(provider.key_placeholder || 'API Key')}" autocomplete="off">
          <label for="${providerBaseUrlId(provider.id)}">接口地址</label>
          <input id="${providerBaseUrlId(provider.id)}" type="text" value="${escapeHtml(settings.base_url || '')}" autocomplete="off">
          ${provider.env_key ? `<p class="hint">也可使用环境变量 ${escapeHtml(provider.env_key)}。</p>` : ''}
        `;
        box.appendChild(card);
      });
    }

    function collectProviderConfig() {
      const providers = {};
      providerOptions.forEach(provider => {
        const apiKeyInput = document.getElementById(providerInputId(provider.id));
        const baseUrlInput = document.getElementById(providerBaseUrlId(provider.id));
        const item = {};
        if (apiKeyInput && apiKeyInput.value.trim()) item.api_key = apiKeyInput.value.trim();
        if (baseUrlInput && baseUrlInput.value.trim()) item.base_url = baseUrlInput.value.trim();
        if (Object.keys(item).length) providers[provider.id] = item;
      });
      return providers;
    }

    function providerInputId(providerId) {
      return `providerKey_${providerId}`;
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
      return {provider: parts[0] || 'deepseek', model: parts[1] || 'deepseek-v4-flash'};
    }

    function modelChoiceValue(provider, model) {
      return `${provider || 'deepseek'}|${model || 'deepseek-v4-flash'}`;
    }

    function selectedModelValue(provider, model) {
      const value = modelChoiceValue(provider, model);
      const known = new Set(modelOptions.map(option => modelChoiceValue(option.provider, option.model)));
      if (known.has(value)) return value;
      const first = modelOptions[0] || {provider: 'deepseek', model: 'deepseek-v4-flash'};
      return modelChoiceValue(first.provider, first.model);
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
      if (mode !== 'semi_agent' && mode !== 'full_agent') return;
      currentMode = mode;
      storeMode(mode);
      updateModeUI();
      setStatus(mode === 'full_agent' ? '已切换到全代理模式。' : '已切换到半代理模式。');
      await refreshWorkflows();
    }

    function updateModeUI() {
      const fullMode = currentMode === 'full_agent';
      document.body.classList.toggle('mode-full', fullMode);
      document.body.classList.toggle('mode-semi', !fullMode);
      document.getElementById('semiModeButton').classList.toggle('active', !fullMode);
      document.getElementById('fullModeButton').classList.toggle('active', fullMode);
      document.getElementById('sidebarTitle').textContent = fullMode ? '项目' : '对话';
      document.getElementById('newProjectButton').hidden = !fullMode;
      if (!fullMode) document.getElementById('sidebarProjectForm').hidden = true;
      document.getElementById('projectHistoryTitle').hidden = !fullMode;
      document.getElementById('sidebarProjects').hidden = !fullMode;
      document.getElementById('sidebarHistoryTitle').hidden = fullMode;
      document.getElementById('sidebarHistory').hidden = fullMode;
      document.getElementById('taskPanelHint').textContent = fullMode ? '显示当前项目的全部任务' : '显示半代理模式的全部任务';
      const note = document.getElementById('modeNote');
      if (fullMode) {
        note.innerHTML = '<strong>全代理模式</strong><span>围绕当前项目工作目录规划，保留项目记忆。</span>';
      } else {
        note.innerHTML = '<strong>半代理模式</strong><span>一次对话处理一个明确任务，不使用项目工作目录。</span>';
      }
      updateProjectStatus();
      renderCurrentModelHint(appState.config);
      ensureSelectedWorkflow();
      renderSidebarItems(cachedWorkflows);
      renderTasks(cachedWorkflows);
      renderConversation(cachedWorkflows);
    }

    async function loadContext() {
      const data = await api('/context');
      applyContextRecord(data.context);
    }

    function applyContextRecord(item) {
      if (!item || !item.value) {
        setState({context: null});
        renderArcMapBridgeState();
        document.getElementById('layerCount').textContent = '0';
        document.getElementById('mxdState').textContent = '未知';
        document.getElementById('srState').textContent = '未知';
        document.getElementById('layerTable').innerHTML = '<tr><td colspan="3">请在 ArcGIS 工具栏点击“同步上下文”。</td></tr>';
        return;
      }
      const ctx = item.value;
      setState({context: ctx});
      const layers = ctx.layers || [];
      const target = activeArcMapBridge();
      const bridgeText = target ? ` · ${arcmapBridgeLabel(target, arcmapBridges.length)}` : '';
      setTile('arcgisState', 'ok', `已同步，${layers.length} 个图层${bridgeText}`);
      document.getElementById('layerCount').textContent = layers.length;
      document.getElementById('mxdState').textContent = ctx.is_saved ? '已保存' : '未保存';
      document.getElementById('srState').textContent = (ctx.spatial_reference && ctx.spatial_reference.name) || '未知';
      const table = document.getElementById('layerTable');
      table.innerHTML = '';
      if (!layers.length) {
        table.innerHTML = '<tr><td colspan="3">当前地图没有可读图层。</td></tr>';
        return;
      }
      layers.forEach(layer => {
        const row = document.createElement('tr');
        row.innerHTML = `<td>${escapeHtml(layer.name || '')}</td><td>${escapeHtml(layer.geometry_type || '')}</td><td>${layer.selected_count || 0}</td>`;
        table.appendChild(row);
      });
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
      if (latestArcgisContext) {
        const layers = latestArcgisContext.layers || [];
        const target = activeArcMapBridge();
        const bridgeText = target ? ` · ${arcmapBridgeLabel(target, arcmapBridges.length)}` : '';
        setTile('arcgisState', 'ok', `已同步，${layers.length} 个图层${bridgeText}`);
        return;
      }
      if (error) {
        setTile('arcgisState', 'bad', '未连接');
        return;
      }
      if (!arcmapBridges.length) {
        setTile('arcgisState', 'bad', '未连接');
        return;
      }
      const target = activeArcMapBridge() || arcmapBridges[0];
      setTile('arcgisState', arcmapBridges.length > 1 ? 'warn' : 'ok', arcmapBridgeLabel(target, arcmapBridges.length));
    }

    function activeArcMapBridge() {
      return arcmapBridges.find(item => item.active) || arcmapBridges.find(item => item.hwnd) || arcmapBridges[0] || null;
    }

    function arcmapBridgeLabel(bridge, count) {
      if (!bridge) return '未连接';
      const summary = bridge.summary || {};
      const title = summary.title || summary.name || 'ArcMap';
      const parts = [title];
      if (bridge.hwnd) parts.push(`hwnd ${bridge.hwnd}`);
      if (bridge.pid) parts.push(`pid ${bridge.pid}`);
      if (count > 1) parts.push(`${count} 个`);
      return parts.join(' · ');
    }

    async function loadProjects() {
      const data = await api('/projects');
      applyProjects(data.projects || [], data.active_project || null);
    }

    function applyProjects(projects, active) {
      setState({projects: projects || [], activeProject: active || null});
      renderApp(['projects']);
    }

    function toggleProjectForm() {
      if (currentMode !== 'full_agent') {
        setStatus('半代理模式不使用项目。切换到全代理模式后再创建项目。');
        return;
      }
      const form = document.getElementById('sidebarProjectForm');
      form.hidden = !form.hidden;
      if (!form.hidden) document.getElementById('projectName').focus();
    }

    async function chooseProjectFolder() {
      if (currentMode !== 'full_agent') {
        setStatus('半代理模式不使用项目。切换到全代理模式后再选择工作目录。');
        return;
      }
      try {
        setStatus('请选择项目工作目录...');
        const data = await api('/dialog/select-folder', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({title: '选择 GeoPilot 项目工作目录'})
        });
        const folder = data.folder || {};
        if (folder.cancelled) {
          setStatus('已取消选择。');
          return;
        }
        if (folder.path) {
          document.getElementById('projectWorkdir').value = folder.path;
          setStatus('已选择项目工作目录。');
        }
      } catch (err) {
        setStatus(err.message);
      }
    }

    async function createProject() {
      if (currentMode !== 'full_agent') {
        setStatus('半代理模式不使用项目。切换到全代理模式后再创建项目。');
        return;
      }
      const name = document.getElementById('projectName').value.trim();
      const workdir = document.getElementById('projectWorkdir').value.trim();
      if (!workdir) {
        setStatus('请选择项目工作目录。');
        return;
      }
      const data = await api('/projects', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, workdir})
      });
      activeProject = data.project;
      document.getElementById('projectName').value = '';
      document.getElementById('projectWorkdir').value = '';
      document.getElementById('sidebarProjectForm').hidden = true;
      setStatus(`当前项目：${activeProject.name}`);
      await loadProjects();
      await refreshWorkflows();
    }

    async function activateProject(id) {
      const data = await api('/projects/active', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({project_id: id})
      });
      activeProject = data.project;
      setStatus(`当前项目：${activeProject.name}`);
      await loadProjects();
      await refreshWorkflows();
    }

    function renderProjects(projects) {
      const sidebar = document.getElementById('sidebarProjects');
      if (!projects.length) {
        sidebar.innerHTML = '<div class="sidebar-session"><div><strong>暂无项目</strong><span>创建项目后会显示在这里</span></div></div>';
        return;
      }
      sidebar.innerHTML = '';
      projects.forEach(project => {
        const active = activeProject && activeProject.id === project.id;
        const node = document.createElement('div');
        node.className = `sidebar-session${active ? ' active' : ''}`;
        const sidebarButton = document.createElement('button');
        sidebarButton.type = 'button';
        sidebarButton.className = `sidebar-project${active ? ' active' : ''}`;
        sidebarButton.onclick = () => activateProject(project.id);
        sidebarButton.innerHTML = `
          <strong>${escapeHtml(project.name)}</strong>
          <span>${escapeHtml(project.workdir)}</span>
        `;
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'sidebar-delete';
        deleteButton.title = '删除项目';
        deleteButton.setAttribute('aria-label', '删除项目');
        deleteButton.textContent = '×';
        deleteButton.onclick = () => deleteProject(project.id, project.name);
        node.appendChild(sidebarButton);
        node.appendChild(deleteButton);
        sidebar.appendChild(node);
      });
    }

    async function deleteProject(id, name) {
      if (!window.confirm(`确定删除项目“${name}”吗？这会清空该项目的对话、记忆和任务记录，但不会删除磁盘文件。`)) return;
      await api(`/projects/${encodeURIComponent(id)}/delete`, {method: 'POST', body: '{}'});
      if (activeProject && activeProject.id === id) {
        activeProject = null;
        selectedWorkflowId = '';
        transientUserMessage = '';
        transientAssistantMessage = '';
      }
      setStatus('项目已删除。');
      await loadProjects();
      await refreshWorkflows();
    }

    function updateProjectStatus() {
      if (currentMode === 'full_agent') {
        setTile('restartState', activeProject ? 'ok' : 'warn', activeProject ? `项目：${activeProject.name}` : '请选择项目');
      } else {
        setTile('restartState', 'ok', '半代理模式');
      }
    }

    async function loadPendingTools() {
      const data = await api('/tools/pending');
      renderPendingTools(data.tools || []);
    }

    function renderPendingTools(tools) {
      const box = document.getElementById('pendingToolsList');
      if (!tools.length) {
        box.innerHTML = '<div class="section-card">暂无自定义工具。</div>';
        return;
      }
      box.innerHTML = '';
      tools.forEach(tool => {
        const statusText = {pending_review: '待审核', enabled: '已启用', rejected: '已拒绝'}[tool.status] || tool.status;
        const card = document.createElement('div');
        card.className = 'compact-item';
        const revision = ((tool.payload || {}).revision || {}).number || 1;
        card.innerHTML = `
          <strong>${escapeHtml(tool.name)} · ${escapeHtml(statusText)}</strong>
          <p>${escapeHtml(tool.capability)}</p>
          <p class="hint">${escapeHtml((tool.payload.operation_spec || {}).id || '')} · rev ${escapeHtml(revision)}</p>
        `;
        if (tool.status === 'pending_review') {
          const actions = document.createElement('div');
          actions.className = 'button-row';
          actions.innerHTML = `
            <button class="success small" onclick="enableTool('${escapeJs(tool.id)}')">启用</button>
            <button class="ghost small" onclick="rejectTool('${escapeJs(tool.id)}')">拒绝</button>
            <button class="danger small" onclick="deleteTool('${escapeJs(tool.id)}')">删除</button>
          `;
          card.appendChild(actions);
        } else {
          const actions = document.createElement('div');
          actions.className = 'button-row';
          actions.innerHTML = `<button class="danger small" onclick="deleteTool('${escapeJs(tool.id)}')">删除</button>`;
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
      if (currentMode === 'full_agent' && !activeProject) {
        setStatus('全代理模式需要先在左侧创建或选择项目工作目录。');
        return;
      }
      input.value = '';
      transientUserMessage = command;
      transientAssistantMessage = '';
      startModelWait('模型正在思考');
      if (currentMode === 'full_agent') {
        renderConversation(cachedWorkflows);
      } else {
        selectedWorkflowId = '';
        renderConversation(cachedWorkflows);
      }
      try {
        setStatus('正在生成任务...');
        const data = await api('/plan', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({command, mode: currentMode, project_id: currentMode === 'full_agent' && activeProject ? activeProject.id : ''})
        });
        transientUserMessage = '';
        transientAssistantMessage = '';
        selectedWorkflowId = data.workflow.id;
        await refreshWorkflows();
      } catch (err) {
        stopModelWait();
        transientAssistantMessage = err.message;
        renderConversation(cachedWorkflows);
        setStatus(err.message);
      } finally {
        stopModelWait();
      }
    }

    async function approve(id) {
      await api(`/workflows/${id}/approve`, {method: 'POST', body: '{}'});
      await api('/arcmap/execute-approved', {method: 'POST', body: JSON.stringify({confirmed: true, allow_edits: true})});
      selectedWorkflowId = id;
      setStatus('已发送到 ArcMap 并自动执行。');
      await refreshWorkflows();
    }

    async function clearConversation() {
      if (currentMode === 'full_agent' && !activeProject) {
        setStatus('全代理模式需要先选择项目，才能清空项目对话。');
        return;
      }
      await api('/workflows/clear', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(clearScope())
      });
      selectedWorkflowId = '';
      transientUserMessage = '';
      transientAssistantMessage = '';
      setStatus(currentMode === 'full_agent' ? '已清空项目对话和上下文。' : '已清空。');
      if (currentMode === 'full_agent') await loadProjects();
      await refreshWorkflows();
    }

    async function deleteWorkflow(id) {
      await api(`/workflows/${id}/delete`, {method: 'POST', body: '{}'});
      if (selectedWorkflowId === id) selectedWorkflowId = '';
      setStatus('已删除。');
      await refreshWorkflows();
    }

    async function repairCustomTool(id) {
      if (repairingWorkflowIds.has(id)) return;
      repairingWorkflowIds.add(id);
      selectedWorkflowId = id;
      transientUserMessage = '让 AI 修这个工具';
      transientAssistantMessage = '';
      startModelWait('AI 正在修订工具');
      renderSidebarItems(cachedWorkflows);
      renderTasks(cachedWorkflows);
      renderConversation(cachedWorkflows);
      try {
        const data = await api(`/workflows/${id}/repair-custom-tool`, {method: 'POST', body: '{}'});
        transientUserMessage = '';
        transientAssistantMessage = '';
        selectedWorkflowId = data.workflow.id;
        setStatus('已生成工具修订，等待审核。');
        await loadPendingTools();
        await refreshWorkflows();
      } catch (err) {
        stopModelWait();
        transientAssistantMessage = err.message;
        setStatus(err.message);
        renderConversation(cachedWorkflows);
      } finally {
        stopModelWait();
        repairingWorkflowIds.delete(id);
        renderSidebarItems(cachedWorkflows);
        renderTasks(cachedWorkflows);
      }
    }

    async function refreshWorkflows(renderChat = true) {
      const data = await api(workflowListPath());
      applyWorkflows(data.workflows || [], renderChat);
    }

    function workflowListPath() {
      const params = new URLSearchParams();
      params.set('limit', '50');
      params.set('mode', currentMode);
      params.set('include_trace', 'false');
      if (currentMode === 'full_agent' && activeProject) params.set('project_id', activeProject.id);
      return `/api/workflows?${params.toString()}`;
    }

    function applyWorkflows(workflows, renderChat = true) {
      setState({workflows: workflows || []});
      pruneTaskDetailsState(cachedWorkflows);
      ensureSelectedWorkflow();
      renderTasks(cachedWorkflows);
      renderSidebarItems(cachedWorkflows);
      if (renderChat) renderConversation(cachedWorkflows);
    }

    async function loadWorkflowDetail(id) {
      const data = await api(`/workflows/${encodeURIComponent(id)}`);
      const detail = data.workflow;
      if (!detail || !detail.id) return;
      const next = cachedWorkflows.slice();
      const index = next.findIndex(item => item.id === detail.id);
      if (index >= 0) next[index] = detail;
      else next.unshift(detail);
      applyWorkflows(next, false);
      renderConversation(cachedWorkflows);
    }
