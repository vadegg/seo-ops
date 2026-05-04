import path from 'node:path';
import { ensureDir, writeJson, writeText } from './files.js';

type RunLogStatus = 'running' | 'completed' | 'failed';

interface RunLogStep {
  at: string;
  title: string;
  details: string;
}

interface RunLogRecord {
  run_id: string;
  command: string;
  workspace_root: string;
  started_at: string;
  finished_at?: string;
  status: RunLogStatus;
  summary?: string;
  results: string[];
  blockers: string[];
  steps: RunLogStep[];
  error_message?: string;
}

function toRunId(date: Date): string {
  return date.toISOString().replace(/[:.]/g, '-');
}

function renderMarkdown(log: RunLogRecord): string {
  const lines: string[] = [];

  lines.push('# AI Run Log');
  lines.push('');
  lines.push(`Run ID: ${log.run_id}`);
  lines.push(`Command: ${log.command}`);
  lines.push(`Workspace: ${log.workspace_root}`);
  lines.push(`Started at: ${log.started_at}`);
  lines.push(`Finished at: ${log.finished_at ?? 'in_progress'}`);
  lines.push(`Status: ${log.status}`);

  if (log.summary) {
    lines.push(`Summary: ${log.summary}`);
  }

  lines.push('');
  lines.push('## Steps');
  lines.push('');

  if (log.steps.length === 0) {
    lines.push('_No steps recorded._');
    lines.push('');
  } else {
    for (const step of log.steps) {
      lines.push(`- ${step.at} — ${step.title}`);
      lines.push(`  ${step.details}`);
    }
    lines.push('');
  }

  lines.push('## Results');
  lines.push('');

  if (log.results.length === 0) {
    lines.push('_No results recorded._');
    lines.push('');
  } else {
    for (const result of log.results) {
      lines.push(`- ${result}`);
    }
    lines.push('');
  }

  lines.push('## Blockers');
  lines.push('');

  if (log.blockers.length === 0 && !log.error_message) {
    lines.push('_No blockers._');
    lines.push('');
  } else {
    for (const blocker of log.blockers) {
      lines.push(`- ${blocker}`);
    }
    if (log.error_message) {
      lines.push(`- Error: ${log.error_message}`);
    }
    lines.push('');
  }

  return `${lines.join('\n').trim()}\n`;
}

export function createRunLogger(command: string, workspaceRoot: string): {
  addStep: (title: string, details: string) => void;
  addResult: (detail: string) => void;
  addBlocker: (detail: string) => void;
  complete: (summary: string) => string;
  fail: (error: unknown, summary?: string) => string;
} {
  const startedAt = new Date();
  const runId = toRunId(startedAt);
  const logsDir = path.join(workspaceRoot, 'reports/ai-runs');
  ensureDir(logsDir);

  const log: RunLogRecord = {
    run_id: runId,
    command,
    workspace_root: workspaceRoot,
    started_at: startedAt.toISOString(),
    status: 'running',
    results: [],
    blockers: [],
    steps: []
  };

  function persist(): string {
    const jsonPath = path.join(logsDir, `${runId}.json`);
    const mdPath = path.join(logsDir, `${runId}.md`);
    writeJson(jsonPath, log);
    writeText(mdPath, renderMarkdown(log));
    return mdPath;
  }

  return {
    addStep(title: string, details: string) {
      log.steps.push({
        at: new Date().toISOString(),
        title,
        details
      });
      persist();
    },
    addResult(detail: string) {
      log.results.push(detail);
      persist();
    },
    addBlocker(detail: string) {
      log.blockers.push(detail);
      persist();
    },
    complete(summary: string) {
      log.status = 'completed';
      log.finished_at = new Date().toISOString();
      log.summary = summary;
      return persist();
    },
    fail(error: unknown, summary?: string) {
      log.status = 'failed';
      log.finished_at = new Date().toISOString();
      log.summary = summary ?? 'Run failed before completion.';
      log.error_message = error instanceof Error ? error.message : String(error);
      return persist();
    }
  };
}
