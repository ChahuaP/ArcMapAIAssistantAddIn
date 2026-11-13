import { existsSync, realpathSync } from 'node:fs';
import { join, relative } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const visualSteps = ['material_reading', 'material_resolved', 'workflow_step_started',
    'workflow_step_finished', 'operation_verified', 'workflow_publishing', 'control_coordinates', 'control_overlay', 'control_export'];

export function watchRun(workspace, requestId, emit) {
    const file = join(workspace, 'workspace.sqlite');
    let database;
    let query;
    let cursor = 0;
    let unavailable = false;

    function poll() {
        if (unavailable || !existsSync(file)) return;
        try {
            if (!query) {
                if (relative(file, realpathSync(file))) throw new Error('Workspace database redirects');
                database = new DatabaseSync(file, { readOnly: true });
                query = database.prepare(
                    'SELECT e.seq, e.run_id, e.time, e.step, e.details FROM operation_requests AS r ' +
                    'JOIN events AS e ON e.run_id = r.run_id WHERE r.key = ? AND e.seq > ? ' +
                    'AND e.step IN (' + visualSteps.map(() => '?').join(',') + ') ORDER BY e.seq LIMIT 100');
            }
            for (const row of query.all(requestId, cursor, ...visualSteps)) {
                cursor = row.seq;
                emit({ kind: 'operation_progress', runId: row.run_id, at: row.time,
                    phase: row.step, details: JSON.parse(row.details) });
            }
        } catch (error) {
            if (error.errcode === 5 || error.errcode === 6) return;
            unavailable = true;
            emit({ kind: 'progress_unavailable' });
        }
    }

    const timer = setInterval(poll, 140);
    timer.unref();
    return () => {
        clearInterval(timer);
        poll();
        database?.close();
    };
}
