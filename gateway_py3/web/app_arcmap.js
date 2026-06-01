    async function openArcMapTargets() {
      openModal('arcmapTargetsModal');
      const box = document.getElementById('arcmapTargetsList');
      box.innerHTML = '<div class="section-card">正在读取...</div>';
      await loadArcMapBridges();
      renderArcMapTargets();
    }

    function renderArcMapTargets() {
      const box = document.getElementById('arcmapTargetsList');
      if (!box) return;
      if (!arcmapBridges.length) {
        box.innerHTML = '<div class="section-card">未检测到 ArcMap Bridge。</div>';
        return;
      }
      box.innerHTML = '';
      arcmapBridges.forEach(bridge => {
        const card = document.createElement('div');
        card.className = 'compact-item';
        const summary = bridge.summary || {};
        card.innerHTML = `
          <strong>${escapeHtml(summary.title || summary.name || 'ArcMap')}</strong>
          <p>hwnd ${escapeHtml(bridge.hwnd || '未知')} · pid ${escapeHtml(bridge.pid || '未知')} · port ${escapeHtml(bridge.port || '未知')}</p>
          <div class="button-row">
            <button class="success small" onclick="selectArcMapBridge(${Number(bridge.pid || 0)}, ${Number(bridge.port || 0)}, ${Number(bridge.hwnd || 0)})">设为目标</button>
          </div>
        `;
        box.appendChild(card);
      });
    }

    async function selectArcMapBridge(pid, port, hwnd) {
      const data = await api('/arcmap/active', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pid, port, hwnd})
      });
      setStatus('已切换 ArcMap 目标。');
      await loadArcMapBridges();
      renderArcMapTargets();
      closeModal('arcmapTargetsModal');
      if (data.bridge) setTile('arcgisState', latestArcgisContext ? 'ok' : 'warn', arcmapBridgeLabel(data.bridge, arcmapBridges.length || 1));
    }
