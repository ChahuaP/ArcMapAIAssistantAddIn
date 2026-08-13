    const API_ORIGIN = window.location.protocol === 'file:' ? 'http://127.0.0.1:8765' : '';
    
    let activeSession = null;
    let eventSource = null;
    let csrfToken = '';
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
    let modelConfigDraft = null;
    const clearedModelKeys = new Set();
    let pendingApprovalRunId = '';
    const approvalDocuments = new Map();
    const appState = {
      config: null,
      health: null,
      runs: [],
      arcmapBridges: []
    };
    const taskDetailsState = new Map();

    function getSessionId() { return activeSession && activeSession.session_id; }
    function getSessionEpoch() { return activeSession && activeSession.epoch; }
    async function loadActiveSession() {
      const response = await fetch(apiUrl('/api/v1/active-session'));
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || response.statusText);
      activeSession = data;
      csrfToken = data.csrf_token;
      return data;
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
      contract_failed: '任务检查失败',
      capability_failed: '能力执行失败',
      infrastructure_failed: '基础设施故障',
      quota_stopped: '模型额度不足',
      model_call_uncertain: '模型调用结果不确定',
      execution_indeterminate: '执行状态不确定',
      publication_indeterminate: '发布状态不确定',
      acceptance_failed: '成果验收失败',
      cancelled: '已取消',
    };

    function stageLabel(stage) {
      return STAGE_LABELS[stage] || stage;
    }

    function requireAppVersion(data) {
      if (!data || typeof data.app_version !== 'string' || !data.app_version.trim()) {
        throw new Error('网关响应缺少 app_version，已拒绝继续。');
      }
      return data.app_version;
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
        stage === 'acceptance_failed' ||
        stage === 'cancelled';
    }

    function isApprovalStage(stage) {
      return stage === 'authorization_required';
    }

    async function resumeIndeterminate(runId) {
      const data = await api(`/api/v1/runs/${runId}/resume`, {method: 'POST', body: '{}'});
      selectedRunId = runId;
      setStatus(stageLabel(data.run.stage));
      await refreshRuns();
    }

    async function submitClarification(runId, clarificationId, answer) {
      const data = await api(`/api/v1/runs/${runId}/clarifications`, {
        method: 'POST', body: JSON.stringify({clarification_id: clarificationId, answer})
      });
      selectedRunId = runId;
      setStatus(stageLabel(data.run.stage));
      await refreshRuns();
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
      if (!activeSession) await loadActiveSession();
      opts.headers = Object.assign({'X-Session-Id': getSessionId(), 'X-Session-Epoch': String(getSessionEpoch())}, opts.headers || {});
      if (opts.method && opts.method.toUpperCase() === 'POST') {
        if (!csrfToken) {
          const session = await fetch(apiUrl('/api/v1/session'), {
            headers: {'X-Session-Id': getSessionId(), 'X-Session-Epoch': String(getSessionEpoch())}
          });
          const sessionData = await session.json();
          if (!session.ok) throw new Error(sessionData.error || session.statusText);
          csrfToken = sessionData.csrf_token;
        }
        opts.headers['X-CSRF-Token'] = csrfToken;
      }
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

    async function openArchivedSessions() {
      openModal('archivesModal');
      const container = document.getElementById('archivesList');
      container.textContent = '正在读取归档...';
      try {
        const data = await api('/api/v1/archived-sessions');
        const sessions = data.sessions || [];
        if (!sessions.length) { container.textContent = '没有已归档对话。'; return; }
        container.innerHTML = sessions.map(item => `<button class="nav-btn" type="button" onclick="showArchivedSession('${escapeJs(item.session_id)}')">${escapeHtml(item.session_id)}（世代 ${escapeHtml(String(item.epoch))}）</button>`).join('');
      } catch (err) { container.textContent = err.message; }
    }

    async function showArchivedSession(sessionId) {
      const container = document.getElementById('archivesList');
      try {
        const data = await api('/api/v1/archived-sessions/' + encodeURIComponent(sessionId));
        const runs = data.runs || [];
        container.innerHTML = `<p><code>${escapeHtml(sessionId)}</code></p>` +
          (runs.length ? runs.map(run => `<p>${escapeHtml(run.command || run.text || run.run_id)}：${escapeHtml(stageLabel(run.stage))}</p>`).join('') : '<p>此归档没有任务。</p>');
      } catch (err) { container.textContent = err.message; }
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

    async function loadDiagnostics() {
      const box = document.getElementById('diagnosticsList');
      box.innerHTML = '<div class="empty-state-card">正在检查...</div>';
      try {
        const data = await api('/api/diagnostics');
        const version = requireAppVersion(data);
        document.getElementById('diagnosticsSummary').textContent = data.ok
          ? `检查通过，当前版本 ${version}。`
          : `发现需要处理的事项，当前版本 ${version}。`;
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
        const version = requireAppVersion(data);
        document.getElementById('capabilitiesSummary').textContent =
          `应用版本 ${version}，${data.operation_count} 个能力。`;
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
      const version = requireAppVersion(data);
      setState({health: data});
      setTile('gatewayState', 'ok', `已启动，${data.operation_count} 个能力`);
      const versionNode = document.getElementById('versionInfo');
      if (versionNode) versionNode.textContent = version;
      updateModeStatus();
      if (!silent) setStatus(`网关已连接，版本 ${version}。`);
    }

    async function loadWorkbenchState() {
      const data = await api('/api/workbench-state');
      try {
        applyHealthData(data.health || {}, true);
        applyConfig(data.config || {});
        applyArcMapBridges((data.arcmap && data.arcmap.bridges) || [], (data.arcmap && data.arcmap.error) || '');
        applyRuns(data.runs || [], true);
        setStatus(`网关已连接，版本 ${requireAppVersion(data.health)}。`);
      } catch (renderErr) {
        console.error('loadWorkbenchState render error:', renderErr);
        throw renderErr;
      }
    }

    async function loadConfig() {
      const data = await api('/config');
      applyConfig(data.config);
    }

    async function saveConfig() {
      const connections = collectModelConnections();
      const agentModelPlan = collectRoleModelPlan();
      const data = await api('/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({connections, agent_model_plan: agentModelPlan})
      });
      clearedModelKeys.clear();
      applyConfig(data.config);
      setStatus(data.restart_required ? '模型配置已保存，重新启动 GeoPilot 后生效。' : '模型配置已保存。');
      closeModal('keyModal');
    }

    function applyConfig(config) {
      if (!config || !Array.isArray(config.connections) ||
          !Array.isArray(config.provider_options) || !config.agent_model_plan) {
        throw new Error('模型配置数据无效，请重启 GeoPilot。');
      }
      for (const connection of config.connections) {
        if (!connection || typeof connection.connection_id !== 'string' ||
            typeof connection.provider_type !== 'string' ||
            typeof connection.endpoint !== 'string' ||
            !Array.isArray(connection.enabled_models) ||
            typeof connection.has_credential !== 'boolean') {
          throw new Error('模型连接数据无效，请重启 GeoPilot。');
        }
      }
      setState({config});
      modelConfigDraft = JSON.parse(JSON.stringify(config));
      const missing = config.connections.filter(item => item.credential_required && !item.has_credential);
      const ok = config.connections.length > 0 && missing.length === 0;
      updateModeUI();
      document.getElementById('keyBadge').textContent = missing.length ?
        `${config.connections.length} 个模型连接，${missing.length} 个缺少 API Key` :
        `${config.connections.length} 个模型连接可用`;
      document.getElementById('keyActionText').textContent = '模型配置';
      setDot('keyDot', ok, !ok);
      renderModelConfiguration();
    }

    function renderModelConfiguration() {
      if (!modelConfigDraft) return;
      const list = document.getElementById('modelConnectionList');
      const labels = Object.fromEntries(modelConfigDraft.provider_options.map(item => [item.provider_type, item.label]));
      list.innerHTML = modelConfigDraft.connections.map(connection => `
        <section class="model-connection-card" data-connection-id="${escapeHtml(connection.connection_id)}">
          <div class="model-connection-header">
            <h3>${escapeHtml(labels[connection.provider_type] || connection.provider_type)} · ${escapeHtml(connection.connection_id)}</h3>
            <button type="button" class="btn btn-danger btn-sm" data-connection-id="${escapeHtml(connection.connection_id)}" onclick="removeModelConnection(this.dataset.connectionId)">删除</button>
          </div>
          <div class="model-connection-grid">
            <label class="model-span-2">接口地址<input data-field="endpoint" value="${escapeHtml(connection.endpoint)}"></label>
            <label class="model-span-2">可用模型<input data-field="models" value="${escapeHtml(connection.enabled_models.join(', '))}" onchange="updateConnectionModels(this)"></label>
            <div class="model-span-2 model-key-row">
              <label>API Key <span class="form-hint">${connection.has_credential ? '已配置' : '未配置'}</span>
                <input data-field="api-key" type="password" placeholder="留空表示不修改" autocomplete="off">
              </label>
              <button type="button" class="btn btn-ghost btn-sm" data-connection-id="${escapeHtml(connection.connection_id)}" onclick="clearModelConnectionKey(this.dataset.connectionId)" ${connection.has_credential ? '' : 'disabled'}>清除</button>
            </div>
          </div>
        </section>`).join('');
      renderProviderOptions();
      renderRoleModelOptions();
    }

    function renderProviderOptions() {
      const select = document.getElementById('newProviderType');
      const current = select.value;
      select.innerHTML = modelConfigDraft.provider_options.map(item =>
        `<option value="${escapeHtml(item.provider_type)}">${escapeHtml(item.label)}</option>`
      ).join('');
      if (current && modelConfigDraft.provider_options.some(item => item.provider_type === current)) {
        select.value = current;
      }
      applyProviderPreset(false);
    }

    function applyProviderPreset(overwrite = true) {
      if (!modelConfigDraft) return;
      const providerType = document.getElementById('newProviderType').value;
      const preset = modelConfigDraft.provider_options.find(item => item.provider_type === providerType);
      if (!preset) return;
      const endpoint = document.getElementById('newConnectionEndpoint');
      const connectionId = document.getElementById('newConnectionId');
      if (overwrite || !endpoint.value) endpoint.value = preset.default_endpoint;
      if (overwrite || !connectionId.value) connectionId.value = `${providerType}-main`;
    }

    function addModelConnection() {
      if (!modelConfigDraft) return;
      syncRolePlanFromControls();
      const providerType = document.getElementById('newProviderType').value;
      const connectionId = document.getElementById('newConnectionId').value.trim();
      const endpoint = document.getElementById('newConnectionEndpoint').value.trim();
      const models = splitModelNames(document.getElementById('newConnectionModels').value);
      const apiKey = document.getElementById('newConnectionKey').value.trim();
      if (!/^[a-z0-9_-]+$/.test(connectionId) || !endpoint || !models.length) {
        setStatus('请填写连接名称、接口地址和至少一个模型。', true);
        return;
      }
      if (modelConfigDraft.connections.some(item => item.connection_id === connectionId)) {
        setStatus('连接名称已存在。', true);
        return;
      }
      const preset = modelConfigDraft.provider_options.find(item => item.provider_type === providerType);
      modelConfigDraft.connections.push({
        connection_id: connectionId,
        provider_type: providerType,
        endpoint,
        enabled_models: models,
        has_credential: Boolean(apiKey),
        credential_required: Boolean(preset && preset.credential_required),
        pending_api_key: apiKey
      });
      document.getElementById('newConnectionModels').value = '';
      document.getElementById('newConnectionKey').value = '';
      renderModelConfiguration();
    }

    function removeModelConnection(connectionId) {
      if (!modelConfigDraft || modelConfigDraft.connections.length === 1) {
        setStatus('至少保留一个模型连接。', true);
        return;
      }
      syncRolePlanFromControls();
      modelConfigDraft.connections = modelConfigDraft.connections.filter(item => item.connection_id !== connectionId);
      clearedModelKeys.delete(connectionId);
      renderModelConfiguration();
    }

    function clearModelConnectionKey(connectionId) {
      if (!window.confirm('确定清除此模型连接的 API Key 吗？')) return;
      clearedModelKeys.add(connectionId);
      const connection = modelConfigDraft.connections.find(item => item.connection_id === connectionId);
      if (connection) {
        connection.has_credential = false;
        delete connection.pending_api_key;
      }
      renderModelConfiguration();
    }

    function updateConnectionModels(input) {
      const card = input.closest('.model-connection-card');
      const connection = modelConfigDraft.connections.find(item => item.connection_id === card.dataset.connectionId);
      const models = splitModelNames(input.value);
      if (!connection || !models.length) {
        setStatus('每个连接至少需要一个可用模型。', true);
        return;
      }
      syncRolePlanFromControls();
      const key = card.querySelector('[data-field="api-key"]').value.trim();
      if (key) connection.pending_api_key = key;
      connection.endpoint = card.querySelector('[data-field="endpoint"]').value.trim();
      connection.enabled_models = models;
      renderModelConfiguration();
    }

    function renderRoleModelOptions() {
      const options = [];
      for (const connection of modelConfigDraft.connections) {
        for (const model of connection.enabled_models) {
          const value = `${encodeURIComponent(connection.connection_id)}:${encodeURIComponent(model)}`;
          options.push({value, label: `${connection.connection_id} / ${model}`});
        }
      }
      const controls = {compiler: 'roleModelCompiler', planner: 'roleModelPlanner', auditor: 'roleModelAuditor', repairer: 'roleModelRepairer'};
      for (const [role, id] of Object.entries(controls)) {
        const select = document.getElementById(id);
        select.innerHTML = options.map(item => `<option value="${item.value}">${escapeHtml(item.label)}</option>`).join('');
        const binding = modelConfigDraft.agent_model_plan[role];
        select.value = `${encodeURIComponent(binding.connection_id)}:${encodeURIComponent(binding.model_id)}`;
      }
    }

    function syncRolePlanFromControls() {
      if (!modelConfigDraft) return;
      modelConfigDraft.agent_model_plan = collectRoleModelPlan();
    }

    function collectRoleModelPlan() {
      const controls = {compiler: 'roleModelCompiler', planner: 'roleModelPlanner', auditor: 'roleModelAuditor', repairer: 'roleModelRepairer'};
      const plan = {};
      for (const [role, id] of Object.entries(controls)) {
        const value = document.getElementById(id).value;
        const separator = value.indexOf(':');
        if (separator < 1) throw new Error('请选择每个环节使用的模型。');
        plan[role] = {
          connection_id: decodeURIComponent(value.slice(0, separator)),
          model_id: decodeURIComponent(value.slice(separator + 1))
        };
      }
      return plan;
    }

    function collectModelConnections() {
      return modelConfigDraft.connections.map(connection => {
        const card = document.querySelector(`.model-connection-card[data-connection-id="${connection.connection_id}"]`);
        const result = {
          connection_id: connection.connection_id,
          provider_type: connection.provider_type,
          endpoint: card.querySelector('[data-field="endpoint"]').value.trim(),
          enabled_models: splitModelNames(card.querySelector('[data-field="models"]').value)
        };
        const key = card.querySelector('[data-field="api-key"]').value.trim() || connection.pending_api_key || '';
        if (key) result.api_key = key;
        if (clearedModelKeys.has(connection.connection_id)) result.clear_api_key = true;
        return result;
      });
    }

    function splitModelNames(value) {
      return [...new Set(String(value || '').split(',').map(item => item.trim()).filter(Boolean))];
    }

    function updateModeUI() {
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
      const select = document.getElementById('arcmapTarget');
      if (select) {
        const saved = localStorage.getItem('geopilot.arcmapTarget') || '';
        select.innerHTML = '<option value="">请选择 ArcMap 目标</option>';
        (bridges || []).forEach((target, index) => {
          const key = `${target.bridge_pid}:${target.arcmap_pid}:${target.hwnd}`;
          const option = document.createElement('option');
          option.value = key;
          option.textContent = `${target.title || 'ArcMap'} (${target.arcmap_pid})${target.active ? ' - 前台' : ''}`;
          option.selected = key === saved;
          select.appendChild(option);
        });
        select.onchange = () => localStorage.setItem('geopilot.arcmapTarget', select.value);
      }
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
      const select = document.getElementById('arcmapTarget');
      const selected = select && select.value;
      return arcmapBridges.find(item => `${item.bridge_pid}:${item.arcmap_pid}:${item.hwnd}` === selected) || null;
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
        // side_effect_level is declared explicitly — execute=True requires it.
        // Level 3 (isolated-workspace write) lets write plans reach the
        // AUTHORIZATION_REQUIRED pause; the human decision + approved_scope is
        // the real gate.
        const target = activeArcMapBridge();
        if (!target) throw new Error('请选择一个已连接的 ArcMap 目标。');
        const payload = {text: command, execute: execute, side_effect_level: 3,
          model_bindings: taskModelBindings(),
          target_selector: {bridge_pid: target.bridge_pid, bridge_port: target.bridge_port,
            arcmap_pid: target.arcmap_pid, hwnd: target.hwnd,
            deployment_hash: target.deployment_hash}};
        const data = await api('/api/v1/runs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        // Reconnect SSE to the new session.
        if (typeof connectEventStream === 'function') connectEventStream();
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

    function taskModelBindings() {
      if (!modelConfigDraft || !modelConfigDraft.agent_model_plan) {
        throw new Error('请先配置每个角色的模型。');
      }
      const connections = new Map(modelConfigDraft.connections.map(item => [item.connection_id, item]));
      const result = {};
      for (const role of ['compiler', 'planner', 'auditor', 'repairer']) {
        const binding = modelConfigDraft.agent_model_plan[role];
        const connection = binding && connections.get(binding.connection_id);
        if (!connection || !connection.enabled_models.includes(binding.model_id)) {
          throw new Error(`角色 ${role} 的模型配置无效。`);
        }
        result[role] = {provider: connection.provider_type, model: binding.model_id};
      }
      return result;
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
        renderApprovalPrompt(runId).catch(err => setStatus(err.message));
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
    // The decision binds the exact plan (plan_digest) and the authorized scope
    // (level + output_id/destination identities). If the plan changes, the decision is invalid.
    window.approveRun = async function(runId) {
      pendingApprovalRunId = '';
      removeApprovalPrompt();
      startModelWait('正在执行', 'authorized');
      try {
        const approval = approvalDocuments.get(runId);
        if (!approval) throw new Error('授权范围尚未加载，拒绝提交。');
        const decideBody = {
          approved: true,
          decision_id: crypto.randomUUID(),
          run_id: runId,
          plan_digest: approval.planDigest,
          approved_scope: {level: approval.riskLevel, inputs: approval.inputs, outputs: approval.outputs},
        };
        const data = await api(`/api/v1/runs/${runId}/decide`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(decideBody)
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
        const approval = approvalDocuments.get(runId);
        if (!approval) throw new Error('授权范围尚未加载，拒绝提交。');
        await api(`/api/v1/runs/${runId}/decide`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({approved: false, decision_id: crypto.randomUUID(),
            run_id: runId, plan_digest: approval.planDigest})
        });
        await refreshRuns();
        setStatus('已拒绝授权。');
      } catch (err) {
        setStatus(err.message);
      }
    }

    async function renderApprovalPrompt(runId) {
      const runView = await api(`/api/v1/runs/${runId}`, {method: 'GET'});
      const run = runView.run || {};
      const steps = (run.workflow && run.workflow.steps) || [];
      const outputs = steps.flatMap(s => (s.declared_outputs || []).map(o => ({
        output_id: o.output_id, destination: o.destination
      })));
      if (!run.plan_digest || !outputs.every(o => o.output_id && o.destination)) {
        throw new Error('封存计划缺少完整输出身份，拒绝显示授权。');
      }
      const inputs = (run.input_identities || []).map((identity, index) => ({input_id: `input-${index}`, identity}));
      if (!Array.isArray(run.input_identities) || !inputs.every(item => item.identity)) {
        throw new Error('封存计划缺少完整输入身份，拒绝显示授权。');
      }
      approvalDocuments.set(runId, {planDigest: run.plan_digest,
        riskLevel: run.risk_level, inputs, outputs});
      const chat = document.getElementById('chatLog');
      const existing = document.getElementById('approvalPrompt');
      if (existing) existing.remove();
      const prompt = document.createElement('div');
      prompt.id = 'approvalPrompt';
      prompt.className = 'approval-prompt';
      prompt.innerHTML = `
        <div class="approval-body">
          <p class="approval-title">⚠️ 执行授权确认</p>
          <p>风险级别：${escapeHtml(String(run.risk_level))}</p>
          <p>输入数据集：</p><ul>${inputs.map(i => `<li><code>${escapeHtml(i.identity)}</code></li>`).join('')}</ul>
          <p>输出：</p><ul>${outputs.map(o => `<li><code>${escapeHtml(o.output_id)}</code><br><code>${escapeHtml(o.destination)}</code></li>`).join('')}</ul>
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
      const data = await api('/api/v1/active-session/clear', {method: 'POST', body: '{}'});
      activeSession = data;
      csrfToken = data.csrf_token;
      if (typeof connectEventStream === 'function') connectEventStream();
      selectedRunId = '';
      transientUserMessage = '';
      transientAssistantMessage = '';
      stopModelWait();
      setStatus('已清空会话。');
      cachedRuns = [];
      renderConversation(cachedRuns);
      renderTasks(cachedRuns);
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
