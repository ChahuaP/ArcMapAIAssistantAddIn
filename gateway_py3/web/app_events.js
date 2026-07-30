    function tagClass(item, action) {
      if (item.status === 'succeeded') return 'done';
      if (action === 'answer') return 'done';
      if (action === 'clarify') return 'clarify';
      if (action === 'unsupported' || item.status === 'failed' || item.status === 'context_failed' || item.status === 'indeterminate') return 'unsupported';
      return 'execute';
    }

    function shortCommand(command) {
      return command.length > 44 ? command.slice(0, 44) + '...' : command;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    function escapeJs(value) {
      return String(value).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    }

    async function refreshAll() {
      try {
        await loadWorkbenchState();
      } catch (err) {
        setTile('gatewayState', 'bad', '未连接');
        setTile('restartState', 'warn', '启动控制台');
        setStatus(err.message);
        renderEmptyChat();
      }
    }

    function connectEventStream() {
      if (!window.EventSource) {
        setStatus('当前浏览器不支持实时事件流。请用新版浏览器打开 GeoPilot。');
        return;
      }
      if (eventSource) eventSource.close();
      eventSource = new EventSource(apiUrl('/events'));
      eventSource.addEventListener('open', () => {
        if (appState.health) {
          applyHealthData(appState.health, true);
        } else {
          setTile('gatewayState', 'ok', '已连接');
        }
      });
      eventSource.addEventListener('error', () => {
        setTile('gatewayState', 'warn', '等待重连');
      });
      ['runs.changed', 'context.changed', 'arcmap.changed', 'config.changed', 'tools.changed', 'catalog.changed'].forEach(type => {
        eventSource.addEventListener(type, () => scheduleEventRefresh(type));
      });
    }

    function scheduleEventRefresh(type) {
      pendingEventTypes.add(eventSlice(type));
      if (eventRefreshTimer) return;
      eventRefreshTimer = window.setTimeout(refreshFromEvents, 80);
    }

    function eventSlice(type) {
      return String(type || '').replace(/\.changed$/, '');
    }

    async function refreshFromEvents() {
      eventRefreshTimer = 0;
      if (eventRefreshBusy) {
        eventRefreshTimer = window.setTimeout(refreshFromEvents, 80);
        return;
      }
      const types = new Set(pendingEventTypes);
      pendingEventTypes.clear();
      eventRefreshBusy = true;
      try {
        if (types.has('config')) await loadConfig();
        if (types.has('catalog')) {
          capabilitiesLoaded = false;
          if (!document.getElementById('capabilitiesModal').hidden) await loadCapabilities();
        }
        if (types.has('arcmap')) await loadArcMapBridges();
        if (types.has('tools') && !document.getElementById('toolsModal').hidden) await loadPendingTools();
        if (types.has('runs') || types.has('context')) await refreshRuns(!transientUserMessage);
      } catch (err) {
        setTile('gatewayState', 'bad', '未连接');
        setTile('restartState', 'warn', '启动控制台');
      } finally {
        eventRefreshBusy = false;
        if (pendingEventTypes.size) {
          eventRefreshTimer = window.setTimeout(refreshFromEvents, 80);
        }
      }
    }

    const commandInput = document.getElementById('command');
    commandInput.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        submitPlan();
      }
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        document.querySelectorAll('.overlay').forEach(modal => { modal.hidden = true; });
      }
    });

    renderEmptyChat();
    refreshAll();
    connectEventStream();
