    function tagClass(item, action) {
      const stage = item.stage || '';
      if (stage === 'succeeded') return 'done';
      if (stage === 'clarification_required') return 'clarify';
      if (stage === 'contract_failed' || stage === 'capability_failed' ||
          stage === 'infrastructure_failed' || stage === 'acceptance_failed' ||
          stage === 'execution_indeterminate' || stage === 'cancelled' ||
          stage === 'policy_denied') return 'unsupported';
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
        await loadArcMapBridges();
        resumeActiveRun();
      } catch (err) {
        setTile('gatewayState', 'bad', '未连接');
        setStatus(err.message);
        renderEmptyChat();
      }
    }

    function resumeActiveRun() {
      // On page refresh, find the latest non-terminal run and resume its
      // modelWait display so the user sees progress instead of stale text.
      const active = cachedRuns.find(r => {
        const s = r.stage || r.status || '';
        return s && !['succeeded','clarification_required','policy_denied',
          'contract_failed','capability_failed','infrastructure_failed',
          'quota_stopped','model_call_uncertain','execution_indeterminate',
          'acceptance_failed','cancelled'].includes(s);
      });
      if (active) {
        selectedRunId = active.run_id || active.id;
        transientUserMessage = active.command || active.text || '';
        transientAssistantMessage = '';
        startModelWait('任务进行中', active.stage || active.status || 'received');
        renderConversation(cachedRuns);
        waitForRunSSE(selectedRunId, active.stage || active.status || 'received');
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
      // §14: consume kernel stage-changed events (SSE projection of run_events).
      eventSource.addEventListener('run.stage_changed', (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.run_id && payload.stage) {
            handleRunStageChanged(payload.run_id, payload.stage);
          }
        } catch (e) { /* ignore malformed event */ }
        // Don't refreshRuns while modelWait is active — it would destroy
        // the progress bubble. handleRunStageChanged handles the UI update.
        if (!modelWait) scheduleEventRefresh('runs');
      });
      eventSource.addEventListener('planning.node_update', () => scheduleEventRefresh('runs'));
      eventSource.addEventListener('arcmap.execution_started', () => scheduleEventRefresh('runs'));
      eventSource.addEventListener('arcmap.execution_done', () => scheduleEventRefresh('runs'));
      ['config.changed', 'catalog.changed', 'arcmap.changed', 'tools.changed'].forEach(type => {
        eventSource.addEventListener(type, () => scheduleEventRefresh(eventSlice(type)));
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
        if (types.has('runs')) await refreshRuns(!transientUserMessage);
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
