    function updateMentionMenu() {
      const input = document.getElementById('command');
      const cursor = input.selectionStart || 0;
      const textBefore = input.value.slice(0, cursor);
      const trigger = activeMentionTrigger(textBefore);
      if (!trigger) {
        hideMentionMenu();
        return;
      }
      const query = textBefore.slice(trigger.index + 1);
      const layerContext = trigger.type === '#' ? nearestMentionedLayer(textBefore.slice(0, trigger.index)) : null;
      const items = trigger.type === '@'
        ? layerMentionItems(query)
        : fieldMentionItems(query, layerContext);
      if (!items.length) {
        hideMentionMenu();
        return;
      }
      mentionState = {
        type: trigger.type,
        start: trigger.index,
        end: cursor,
        query,
        layerContext,
        items,
        activeIndex: 0
      };
      renderMentionMenu();
    }

    function activeMentionTrigger(textBefore) {
      const at = textBefore.lastIndexOf('@');
      const hash = textBefore.lastIndexOf('#');
      const index = Math.max(at, hash);
      if (index < 0) return null;
      const query = textBefore.slice(index + 1);
      if (/[\s，。；、,;:：]/.test(query)) return null;
      return {type: textBefore[index], index};
    }

    function layerMentionItems(query) {
      const needle = query.trim().toLowerCase();
      return currentLayers()
        .filter(layer => !needle || layerSearchText(layer).includes(needle))
        .slice(0, 20)
        .map(layer => ({
          kind: 'layer',
          label: layer.name || layer.longName || layer.layer_ref || '',
          detail: layer.geometry_type || layer.dataSource || '',
          insert: '@' + (layer.name || layer.longName || layer.layer_ref || '')
        }));
    }

    function fieldMentionItems(query, layerContext) {
      const needle = query.trim().toLowerCase();
      const layers = layerContext ? [layerContext] : currentLayers();
      const items = [];
      layers.forEach(layer => {
        (layer.fields || []).forEach(field => {
          const name = field.name || '';
          if (!name || (needle && !name.toLowerCase().includes(needle))) return;
          items.push({
            kind: 'field',
            label: name,
            detail: layer.name || layer.longName || layer.layer_ref || '',
            insert: layerContext ? '#' + name : '@' + (layer.name || layer.longName || layer.layer_ref || '') + ' #' + name
          });
        });
      });
      return items.slice(0, 30);
    }

    function nearestMentionedLayer(textBefore) {
      let best = null;
      currentLayers().forEach(layer => {
        const name = layer.name || layer.longName || layer.layer_ref || '';
        if (!name) return;
        const index = textBefore.lastIndexOf('@' + name);
        if (index >= 0 && (!best || index > best.index)) {
          best = {index, layer};
        }
      });
      return best ? best.layer : null;
    }

    function currentLayers() {
      return (latestArcgisContext && latestArcgisContext.layers) || [];
    }

    function layerSearchText(layer) {
      return [
        layer.name,
        layer.longName,
        layer.layer_ref,
        layer.dataSource,
        layer.geometry_type
      ].filter(Boolean).join(' ').toLowerCase();
    }

    function renderMentionMenu() {
      const menu = document.getElementById('mentionMenu');
      menu.hidden = false;
      menu.innerHTML = '';
      mentionState.items.forEach((item, index) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = index === mentionState.activeIndex ? 'active' : '';
        button.innerHTML = `
          <strong>${escapeHtml(item.label)}</strong>
          <span>${escapeHtml(item.kind === 'field' ? '字段 · ' + item.detail : item.detail)}</span>
        `;
        button.onmousedown = event => {
          event.preventDefault();
          insertMention(index);
        };
        menu.appendChild(button);
      });
    }

    function moveMentionSelection(delta) {
      if (!mentionState) return;
      const count = mentionState.items.length;
      mentionState.activeIndex = (mentionState.activeIndex + delta + count) % count;
      renderMentionMenu();
    }

    function insertMention(index) {
      if (!mentionState) return;
      const input = document.getElementById('command');
      const item = mentionState.items[index];
      const before = input.value.slice(0, mentionState.start);
      const after = input.value.slice(mentionState.end);
      const prefix = before && !/[\s，。；、,;:：]$/.test(before) ? ' ' : '';
      const suffix = after && !/^[\s，。；、,;:：]/.test(after) ? ' ' : '';
      const inserted = prefix + item.insert + suffix;
      input.value = before + inserted + after;
      const cursor = before.length + inserted.length;
      input.focus();
      input.setSelectionRange(cursor, cursor);
      hideMentionMenu();
    }

    function hideMentionMenu() {
      mentionState = null;
      const menu = document.getElementById('mentionMenu');
      if (menu) menu.hidden = true;
    }

    function handleMentionKeydown(event) {
      if (!mentionState) return false;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        moveMentionSelection(1);
        return true;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        moveMentionSelection(-1);
        return true;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        insertMention(mentionState.activeIndex);
        return true;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        hideMentionMenu();
        return true;
      }
      return false;
    }

    function tagClass(item, action) {
      if (item.status === 'succeeded') return 'done';
      if (action === 'answer') return 'done';
      if (action === 'clarify') return 'clarify';
      if (action === 'unsupported' || item.status === 'failed') return 'unsupported';
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
        await loadConfig();
        await openHealth({silent: true});
        await loadArcMapBridges();
        await loadProjects();
        await loadContext();
        await refreshWorkflows();
      } catch (err) {
        setTile('gatewayState', 'bad', '未连接');
        setTile('restartState', 'warn', '请启动网关');
        setStatus(err.message);
        renderEmptyChat();
      }
    }

    async function pollUpdates() {
      if (pollBusy) return;
      pollBusy = true;
      try {
        if (Date.now() - lastHealthCheck > HEALTH_INTERVAL_MS) {
          await loadConfig();
          await openHealth({silent: true});
          await loadArcMapBridges();
        }
        await loadProjects();
        await loadContext();
        await refreshWorkflows(false);
      } catch (err) {
        setTile('gatewayState', 'bad', '未连接');
        setTile('restartState', 'warn', '请启动网关');
      } finally {
        pollBusy = false;
      }
    }

    const commandInput = document.getElementById('command');
    commandInput.addEventListener('keydown', event => {
      if (handleMentionKeydown(event)) return;
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        submitPlan();
      }
    });
    commandInput.addEventListener('input', updateMentionMenu);
    commandInput.addEventListener('click', updateMentionMenu);
    commandInput.addEventListener('keyup', event => {
      if (!['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(event.key)) {
        updateMentionMenu();
      }
    });
    commandInput.addEventListener('blur', () => {
      setTimeout(hideMentionMenu, 120);
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        hideMentionMenu();
        document.querySelectorAll('.modal').forEach(modal => { modal.hidden = true; });
      }
    });

    renderEmptyChat();
    refreshAll();
    setInterval(pollUpdates, POLL_INTERVAL_MS);
