    function renderConversation(runs) {
      const chat = document.getElementById('chatLog');
      chat.innerHTML = '';
      const items = visibleRuns(runs).slice().reverse();
      if (!items.length && !transientUserMessage) {
        renderEmptyChat();
        return;
      }
      items.forEach(item => {
        appendBubble('user', item.command || item.text || '', false);
        appendAssistantForRun(item, false, false);
      });
      appendTransientConversation(false);
      chat.scrollTop = chat.scrollHeight;
      const selected = selectedRun(runs);
      if (selected) setStatus(stageLabel(selected.stage));
    }

    function appendTransientConversation(scroll = true) {
      if (!transientUserMessage) return;
      appendBubble('user', transientUserMessage, false);
      if (modelWait && !transientAssistantMessage) {
        appendModelWaitBubble(scroll);
      } else {
        appendBubble('assistant', transientAssistantMessage || '正在处理...', scroll);
      }
    }

    function renderEmptyChat() {
      const chat = document.getElementById('chatLog');
      chat.innerHTML = `<div class="chat-empty"><div><p class="chat-empty-title">等待任务</p><span class="chat-empty-hint">${escapeHtml(taskScopeText())}</span></div></div>`;
    }

    function removeEmptyChat() {
      const empty = document.querySelector('.chat-empty');
      if (empty) empty.remove();
    }

    function appendAssistantForRun(item, scroll = true, updateStatus = true) {
      const stage = item.stage || '';
      const outcome = item.outcome || {};
      let text = outcome.message || stageLabel(stage);
      if (stage === 'authorization_required') {
        text = '任务已规划，等待授权确认。';
      } else if (stage === 'clarification_required') {
        text += '\n\n信息不够，当前不会执行任何操作。';
      } else if (isTerminalStage(stage) && stage !== 'succeeded') {
        text += '\n\n任务未成功完成。';
      }
      appendBubble('assistant', text, scroll);
      if (updateStatus) setStatus(stageLabel(stage));
    }

    function appendBubble(role, text, scroll = true) {
      removeEmptyChat();
      const chat = document.getElementById('chatLog');
      const row = document.createElement('div');
      row.className = `bubble-row ${role === 'user' ? 'user' : 'assistant'}`;
      const bubble = document.createElement('div');
      bubble.className = `bubble ${role === 'assistant' ? '' : 'plain'}`;
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
      bubble.className = 'bubble';
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

    function renderTasks(runs) {
      const box = document.getElementById('tasks');
      box.innerHTML = '';
      const items = visibleRuns(runs);
      if (!items.length) {
        const text = '暂无任务。';
        box.innerHTML = `<div class="empty-state-card">${text}</div>`;
        return;
      }
      const list = document.createElement('div');
      list.className = 'task-list';
      items.forEach(item => list.appendChild(taskCard(item)));
      box.appendChild(list);
    }

    function ensureSelectedRun() {
      const visible = visibleRuns(cachedRuns);
      if (!visible.length) {
        selectedRunId = '';
        return;
      }
      if (transientUserMessage && !selectedRunId) return;
      if (!visible.some(item => item.id === selectedRunId)) {
        selectedRunId = visible[0].id;
      }
    }

    function selectedRun(runs) {
      return visibleRuns(runs).find(item => item.id === selectedRunId) || null;
    }

    function visibleRuns(runs) {
      return runs || [];
    }

    function clearScope() {
      return {};
    }

    function taskCard(item) {
      const action = item.workflow.action || 'execute';
      const card = document.createElement('div');
      card.className = `task-card${item.id === selectedRunId ? ' active' : ''}`;
      card.innerHTML = `
        <div class="task-top">
          <span class="tag ${tagClass(item, action)}">${statusLabel(item, action)}</span>
          ${item.id === selectedRunId ? '<span class="task-current">当前</span>' : ''}
        </div>
        <p class="task-title">${escapeHtml(workflowTitle(item.workflow))}</p>
        <div class="task-meta">${escapeHtml(shortCommand(item.command))}</div>
        ${action === 'answer' ? '<div class="task-note">这是一条普通回复，不需要发送到 ArcGIS。</div>' : ''}
        ${failedMessage(item)}
        ${writesData(item.workflow) ? '<div class="task-note warn">这个任务会写出新数据。若当前 MXD 未保存，需要在对话中说明输出文件夹或 GDB。</div>' : ''}
      `;

      const steps = document.createElement('details');
      restoreTaskDetailsState(steps, item.id, 'steps', item.stage === 'plan_verified');
      steps.innerHTML = `<summary>执行步骤</summary><ol class="task-steps">${stepItems(item.workflow)}</ol>`;
      card.appendChild(steps);

      if (item.stage === 'execution_indeterminate' || item.stage === 'publication_indeterminate') {
        const resume = document.createElement('button');
        resume.className = 'btn btn-sm btn-warn';
        resume.textContent = '人工确认后继续核对';
        resume.addEventListener('click', () => resumeIndeterminate(item.id).catch(err => setStatus(err.message)));
        card.appendChild(resume);
      }

      const tech = document.createElement('details');
      restoreTaskDetailsState(tech, item.id, 'tech', false);
      tech.innerHTML = `<summary>技术详情</summary><pre>${escapeHtml(JSON.stringify(item.workflow, null, 2))}</pre>`;
      card.appendChild(tech);
      return card;
    }

    function restoreTaskDetailsState(details, runId, panel, defaultOpen) {
      const saved = taskDetailsState.get(runId);
      details.open = saved && Object.prototype.hasOwnProperty.call(saved, panel)
        ? saved[panel]
        : defaultOpen;
      details.addEventListener('toggle', () => {
        const state = taskDetailsState.get(runId) || {};
        state[panel] = details.open;
        taskDetailsState.set(runId, state);
      });
    }

    function pruneTaskDetailsState(runs) {
      const ids = new Set((runs || []).map(item => item.id));
      Array.from(taskDetailsState.keys()).forEach(id => {
        if (!ids.has(id)) taskDetailsState.delete(id);
      });
    }

    function stepItems(workflow) {
      const steps = workflow.steps || [];
      if (!steps.length) return '<li>没有执行步骤。</li>';
      return steps.map(step => `<li>${escapeHtml(step.reason || step.operation)}</li>`).join('');
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
      ].includes(step.operation));
    }

    function failedMessage(item) {
      if (item.stage === 'execution_indeterminate') return '<div class="task-note error">ArcMap 执行后的权威结果无法判定；请人工确认后继续核对，系统不会重跑。</div>';
      if (item.stage === 'publication_indeterminate') return '<div class="task-note error">发布结果无法判定；请人工确认后继续核对，系统不会重跑。</div>';
      if (item.stage === 'infrastructure_failed') return '<div class="task-note error">基础设施故障。</div>';
      if (item.outcome && item.outcome.kind !== 'Succeeded' && item.outcome.message) {
        return `<div class="task-note error">${escapeHtml(item.outcome.message)}</div>`;
      }
      return '';
    }

    function statusText(item) {
      return stageLabel(item.stage || item.status || '');
    }

    function statusLabel(item, action) {
      return stageLabel(item.stage || item.status || '');
    }
