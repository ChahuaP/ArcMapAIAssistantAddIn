    function renderConversation(workflows) {
      const chat = document.getElementById('chatLog');
      chat.innerHTML = '';
      if (currentMode === 'full_agent') {
        const items = visibleWorkflows(workflows).slice().reverse();
        if (!items.length && !transientUserMessage) {
          renderEmptyChat();
          return;
        }
        items.forEach(item => {
          appendBubble('user', item.command, false);
          appendAssistantForWorkflow(item, false, false);
        });
        appendTransientConversation(false);
        chat.scrollTop = chat.scrollHeight;
        const selected = selectedWorkflow(workflows);
        if (selected && !repairingWorkflowIds.has(selected.id)) setStatus(statusText(selected));
        return;
      }
      const item = selectedWorkflow(workflows);
      if (!item && !transientUserMessage) {
        renderEmptyChat();
        return;
      }
      if (item) {
        appendBubble('user', item.command, false);
        appendAssistantForWorkflow(item, false);
      }
      appendTransientConversation(false);
      chat.scrollTop = chat.scrollHeight;
    }

    function appendTransientConversation(scroll = true) {
      if (!transientUserMessage) return;
      appendBubble('user', transientUserMessage, false);
      if (modelWait && !transientAssistantMessage) {
        appendModelWaitBubble(scroll);
      } else {
        appendBubble('assistant', transientAssistantMessage || '正在思考...', scroll);
      }
    }

    function renderEmptyChat() {
      const chat = document.getElementById('chatLog');
      chat.innerHTML = '<div class="empty-chat"><div><strong>等待任务</strong><span>在下方输入你想完成的 GIS 操作。</span></div></div>';
    }

    function removeEmptyChat() {
      const empty = document.querySelector('.empty-chat');
      if (empty) empty.remove();
    }

    function appendAssistantForWorkflow(item, scroll = true, updateStatus = true) {
      const wf = item.workflow;
      const action = wf.action || 'execute';
      let text = wf.summary;
      if (action === 'execute') {
        text += item.status === 'draft'
          ? '\n\n已生成任务，等待执行确认。'
          : '\n\n任务已发送到 ArcMap。';
      } else if (action === 'clarify') {
        text += '\n\n信息不够，当前不会执行任何操作。';
      } else if (action === 'unsupported') {
        text += '\n\n当前版本还没有这个能力。';
      }
      appendBubble('assistant', text, scroll);
      if (updateStatus) setStatus(statusText(item));
    }

    function appendBubble(role, text, scroll = true) {
      removeEmptyChat();
      const chat = document.getElementById('chatLog');
      const row = document.createElement('div');
      row.className = `bubble-row ${role === 'user' ? 'user' : 'assistant'}`;
      const bubble = document.createElement('div');
      bubble.className = `bubble ${role === 'assistant' ? 'markdown-bubble' : 'plain-bubble'}`;
      if (role === 'assistant') {
        bubble.innerHTML = renderAssistantMarkdown(text);
      } else {
        bubble.textContent = text;
      }
      row.appendChild(bubble);
      chat.appendChild(row);
      if (scroll) chat.scrollTop = chat.scrollHeight;
    }

    function appendModelWaitBubble(scroll = true) {
      removeEmptyChat();
      const chat = document.getElementById('chatLog');
      const row = document.createElement('div');
      row.className = 'bubble-row assistant';
      const bubble = document.createElement('div');
      bubble.id = 'modelWaitBubble';
      bubble.className = 'bubble markdown-bubble';
      bubble.innerHTML = renderModelWait();
      row.appendChild(bubble);
      chat.appendChild(row);
      if (scroll) chat.scrollTop = chat.scrollHeight;
    }

    function renderAssistantMarkdown(text) {
      const parsed = splitThinking(text);
      const body = parsed.body || '已生成回复。';
      let html = `<div class="markdown-body">${renderMarkdown(body)}</div>`;
      parsed.thoughts.forEach((thought, index) => {
        html += `
          <details class="think-panel">
            <summary>${parsed.thoughts.length > 1 ? `思考过程 ${index + 1}` : '思考过程'}</summary>
            <div class="think-content markdown-body">${renderMarkdown(thought)}</div>
          </details>
        `;
      });
      return html;
    }

    function splitThinking(text) {
      const thoughts = [];
      const body = String(text || '').replace(/<think>([\s\S]*?)<\/think>/gi, (_match, content) => {
        if (String(content || '').trim()) thoughts.push(String(content).trim());
        return '';
      }).trim();
      return {body, thoughts};
    }

    function renderMarkdown(text) {
      const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
      const html = [];
      let paragraph = [];
      let listType = '';
      let inFence = false;
      let fenceLines = [];

      const flushParagraph = () => {
        if (!paragraph.length) return;
        html.push(`<p>${renderInlineMarkdown(paragraph.join(' '))}</p>`);
        paragraph = [];
      };
      const closeList = () => {
        if (!listType) return;
        html.push(`</${listType}>`);
        listType = '';
      };
      const openList = type => {
        if (listType === type) return;
        closeList();
        listType = type;
        html.push(`<${type}>`);
      };

      lines.forEach(line => {
        const trimmed = line.trim();
        if (trimmed.startsWith('```')) {
          if (inFence) {
            html.push(`<pre><code>${escapeHtml(fenceLines.join('\n'))}</code></pre>`);
            fenceLines = [];
            inFence = false;
          } else {
            flushParagraph();
            closeList();
            inFence = true;
          }
          return;
        }
        if (inFence) {
          fenceLines.push(line);
          return;
        }
        if (!trimmed) {
          flushParagraph();
          closeList();
          return;
        }
        const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
        if (heading) {
          flushParagraph();
          closeList();
          const level = heading[1].length;
          html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
          return;
        }
        const unordered = /^[-*]\s+(.+)$/.exec(trimmed);
        if (unordered) {
          flushParagraph();
          openList('ul');
          html.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
          return;
        }
        const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed);
        if (ordered) {
          flushParagraph();
          openList('ol');
          html.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
          return;
        }
        const quote = /^>\s?(.+)$/.exec(trimmed);
        if (quote) {
          flushParagraph();
          closeList();
          html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
          return;
        }
        closeList();
        paragraph.push(trimmed);
      });

      if (inFence) html.push(`<pre><code>${escapeHtml(fenceLines.join('\n'))}</code></pre>`);
      flushParagraph();
      closeList();
      return html.join('') || '<p></p>';
    }

    function renderInlineMarkdown(text) {
      const codeTokens = [];
      let safe = escapeHtml(text).replace(/`([^`]+)`/g, (_match, code) => {
        const token = `@@CODE${codeTokens.length}@@`;
        codeTokens.push(`<code>${code}</code>`);
        return token;
      });
      safe = safe.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_match, label, url) => {
        return `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${label}</a>`;
      });
      safe = safe.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      safe = safe.replace(/__([^_]+)__/g, '<strong>$1</strong>');
      safe = safe.replace(/\*([^*]+)\*/g, '<em>$1</em>');
      safe = safe.replace(/_([^_]+)_/g, '<em>$1</em>');
      codeTokens.forEach((tokenHtml, index) => {
        safe = safe.replace(`@@CODE${index}@@`, tokenHtml);
      });
      return safe;
    }

    function workflowTitle(workflow) {
      const parsed = splitThinking((workflow || {}).summary || '');
      return stripMarkdown(parsed.body || (workflow || {}).summary || '').trim() || '任务';
    }

    function stripMarkdown(text) {
      return String(text || '')
        .replace(/```[\s\S]*?```/g, ' ')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/[*_#>`-]/g, ' ')
        .replace(/\s+/g, ' ');
    }

    function renderTasks(workflows) {
      const box = document.getElementById('tasks');
      box.innerHTML = '';
      const items = visibleWorkflows(workflows);
      if (!items.length) {
        const text = currentMode === 'full_agent'
          ? '当前会话暂无任务。'
          : '暂无任务。';
        box.innerHTML = `<div class="section-card">${text}</div>`;
        return;
      }
      const list = document.createElement('div');
      list.className = 'task-list';
      items.forEach(item => list.appendChild(taskCard(item)));
      box.appendChild(list);
    }

    function ensureSelectedWorkflow() {
      const visible = visibleWorkflows(cachedWorkflows);
      if (!visible.length) {
        selectedWorkflowId = '';
        return;
      }
      if (transientUserMessage && currentMode !== 'full_agent' && !selectedWorkflowId) return;
      if (!visible.some(item => item.id === selectedWorkflowId)) {
        selectedWorkflowId = visible[0].id;
      }
    }

    function selectedWorkflow(workflows) {
      return visibleWorkflows(workflows).find(item => item.id === selectedWorkflowId) || null;
    }

    function workflowMode(item) {
      return item.mode || 'semi_agent';
    }

    function visibleWorkflows(workflows) {
      return (workflows || []).filter(item => {
        return workflowMode(item) === currentMode;
      });
    }

    function clearScope() {
      return {mode: currentMode};
    }

    function taskCard(item) {
      const action = item.workflow.action || 'execute';
      const card = document.createElement('div');
      card.className = `task-card${item.id === selectedWorkflowId ? ' active' : ''}`;
      card.innerHTML = `
        <div class="task-top">
          <span class="tag ${tagClass(item, action)}">${statusLabel(item, action)}</span>
          ${item.id === selectedWorkflowId ? '<span class="task-meta">当前</span>' : ''}
        </div>
        <p class="task-title">${escapeHtml(workflowTitle(item.workflow))}</p>
        <div class="task-meta">${escapeHtml(shortCommand(item.command))}</div>
        ${action === 'answer' ? '<div class="task-note">这是一条普通回复，不需要发送到 ArcGIS。</div>' : ''}
        ${item.status === 'approved_for_arcmap' ? '<div class="task-note">已发送到 ArcMap，等待自动执行。</div>' : ''}
        ${failedMessage(item)}
        ${repairingWorkflowIds.has(item.id) ? '<div class="task-note">AI 正在读取失败信息并修订原工具...</div>' : ''}
        ${writesData(item.workflow) ? '<div class="task-note warn">这个任务会写出新数据。若当前 MXD 未保存，需要在对话中说明输出文件夹或 GDB。</div>' : ''}
      `;
      const actions = document.createElement('div');
      actions.className = 'button-row';
      if (item.status === 'draft' && action === 'execute') {
        const approveButton = document.createElement('button');
        approveButton.className = 'success small';
        approveButton.textContent = '发送并执行';
        approveButton.onclick = () => approve(item.id);
        actions.appendChild(approveButton);
      }
      if (item.status === 'failed' && usesCustomTool(item.workflow)) {
        const repairButton = document.createElement('button');
        const repairPending = repairingWorkflowIds.has(item.id);
        repairButton.className = 'success small';
        repairButton.textContent = repairPending ? '修复中...' : '让 AI 修这个工具';
        repairButton.disabled = repairPending;
        repairButton.onclick = () => repairCustomTool(item.id);
        actions.appendChild(repairButton);
      }
      const deleteButton = document.createElement('button');
      deleteButton.className = 'danger small';
      deleteButton.textContent = '删除';
      deleteButton.onclick = () => deleteWorkflow(item.id);
      actions.appendChild(deleteButton);
      card.appendChild(actions);

      const steps = document.createElement('details');
      restoreTaskDetailsState(steps, item.id, 'steps', item.status === 'draft');
      steps.innerHTML = `<summary>执行步骤</summary>${stepList(item.workflow)}`;
      card.appendChild(steps);

      const tech = document.createElement('details');
      restoreTaskDetailsState(tech, item.id, 'tech', false);
      tech.innerHTML = `<summary>技术详情</summary><pre>${escapeHtml(JSON.stringify(item.workflow, null, 2))}</pre>`;
      card.appendChild(tech);
      return card;
    }

    function restoreTaskDetailsState(details, workflowId, panel, defaultOpen) {
      const saved = taskDetailsState.get(workflowId);
      details.open = saved && Object.prototype.hasOwnProperty.call(saved, panel)
        ? saved[panel]
        : defaultOpen;
      details.addEventListener('toggle', () => {
        const state = taskDetailsState.get(workflowId) || {};
        state[panel] = details.open;
        taskDetailsState.set(workflowId, state);
      });
    }

    function pruneTaskDetailsState(workflows) {
      const ids = new Set((workflows || []).map(item => item.id));
      Array.from(taskDetailsState.keys()).forEach(id => {
        if (!ids.has(id)) taskDetailsState.delete(id);
      });
    }

    function stepList(workflow) {
      const steps = workflow.steps || [];
      if (!steps.length) return '<div class="task-meta">没有执行步骤。</div>';
      return '<ol class="task-meta">' + steps.map(step => `<li>${escapeHtml(step.reason || step.operation)}</li>`).join('') + '</ol>';
    }

    function writesData(workflow) {
      return (workflow.steps || []).some(step => [
        'analysis.buffer',
        'analysis.clip',
        'analysis.intersect',
        'analysis.dissolve',
        'analysis.project',
        'analysis.spatial_join',
        'selection.export_selected_features',
        'export.map_png',
        'export.map_pdf',
        'export.table_csv',
        'export.layer_kml',
        'export.split_by_field'
      ].includes(step.operation));
    }

    function usesCustomTool(workflow) {
      return (workflow.steps || []).some(step => typeof step.operation === 'string' && step.operation.startsWith('custom.'));
    }

    function failedMessage(item) {
      if (item.status !== 'failed' || !item.result || !item.result.error) return '';
      return `<div class="task-note error">执行失败：${escapeHtml(item.result.error)}</div>`;
    }

    function statusText(item) {
      const action = item.workflow.action || 'execute';
      if (action === 'clarify') return '需要补充信息。';
      if (action === 'unsupported') return '暂不支持。';
      if (action === 'answer') return '已回答。';
      if (item.status === 'draft') return '任务已生成，待执行。';
      if (item.status === 'approved_for_arcmap') return '已发送到 ArcMap，等待自动执行。';
      return statusLabel(item, action);
    }

    function statusLabel(item, action) {
      if (action === 'answer') return '已回答';
      if (action === 'clarify') return '需要补充';
      if (action === 'unsupported') return '暂不支持';
      if (item.status === 'draft') return '待执行';
      if (item.status === 'approved_for_arcmap') return '待执行';
      if (item.status === 'claimed_by_arcmap' || item.status === 'executing') return '执行中';
      if (item.status === 'succeeded') return '已完成';
      if (item.status === 'failed') return usesCustomTool(item.workflow) ? '失败，可修复' : '失败';
      return item.status;
    }
