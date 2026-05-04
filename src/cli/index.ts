import { loadEnvironment } from '../lib/env.js';
import { parseArgs } from '../lib/cli.js';
import { runBacklogAudit } from '../modules/backlog-audit/index.js';
import { runDailyRunner } from '../modules/daily-runner/index.js';
import { runStageRunner } from '../modules/stage-runner/index.js';

function getRootDir(): string {
  return process.cwd();
}

function resolveWorkspaceRootFlag(flags: Record<string, string | boolean>): string | undefined {
  const fromFlag = typeof flags['workspace-root'] === 'string' ? flags['workspace-root'] : undefined;
  return fromFlag ?? process.env.SEO_WORKSPACE_ROOT;
}

async function runBacklog(rootDir: string, flags: Record<string, string | boolean>): Promise<void> {
  const minReadyTopicsArg = typeof flags['min-ready-topics'] === 'string' ? flags['min-ready-topics'] : undefined;
  const minReadyTopics = minReadyTopicsArg ? Number.parseInt(minReadyTopicsArg, 10) : undefined;
  if (minReadyTopicsArg && Number.isNaN(minReadyTopics)) {
    throw new Error(`Invalid --min-ready-topics value: ${minReadyTopicsArg}`);
  }

  const result = runBacklogAudit(rootDir, {
    workspaceRoot: resolveWorkspaceRootFlag(flags),
    minReadyTopics
  });

  console.log(`Backlog audit written to ${result.workspaceRoot}/reports/status/backlog-audit.md`);
  console.log(`Ready now: ${result.readyCount}`);
  console.log(`Can replenish now: ${result.replenishNowCount}`);
  console.log(`Needs evidence ingest: ${result.needsIngestCount}`);
  console.log(`Backlog health: ${result.healthy ? 'healthy' : 'needs replenishment'}`);
}

async function runDaily(rootDir: string, flags: Record<string, string | boolean>): Promise<void> {
  const minReadyTopicsArg = typeof flags['min-ready-topics'] === 'string' ? flags['min-ready-topics'] : undefined;
  const minReadyTopics = minReadyTopicsArg ? Number.parseInt(minReadyTopicsArg, 10) : undefined;
  if (minReadyTopicsArg && Number.isNaN(minReadyTopics)) {
    throw new Error(`Invalid --min-ready-topics value: ${minReadyTopicsArg}`);
  }

  const result = await runDailyRunner(rootDir, {
    workspaceRoot: resolveWorkspaceRootFlag(flags),
    minReadyTopics,
    dryRun: Boolean(flags['dry-run']),
    runStages: Boolean(flags['run-stages']),
    forceHumanBypass: Boolean(flags['force-human-bypass']),
    publish: Boolean(flags['publish']),
    push: Boolean(flags['push'])
  });

  console.log(`Daily runner report written to ${result.reportPath}`);
  console.log(`AI run log written to ${result.logPath}`);
  console.log(`Runner status: ${result.status}`);
  if (result.articleId) {
    console.log(`Article: ${result.articleId}`);
  }
  if (result.workItemPath) {
    console.log(`Work item path: ${result.workItemPath}`);
  }
}

async function runStage(rootDir: string, flags: Record<string, string | boolean>): Promise<void> {
  const result = await runStageRunner(rootDir, {
    workspaceRoot: resolveWorkspaceRootFlag(flags),
    articleId: typeof flags['article-id'] === 'string' ? flags['article-id'] : undefined,
    dryRun: Boolean(flags['dry-run']),
    forceHumanBypass: Boolean(flags['force-human-bypass']),
    publish: Boolean(flags['publish']),
    push: Boolean(flags['push'])
  });

  console.log(`Stage runner report written to ${result.reportPath}`);
  console.log(`AI run log written to ${result.logPath}`);
  console.log(`Runner status: ${result.status}`);
  console.log(`Final stage: ${result.finalStage}`);
  console.log(`Reason: ${result.reason}`);
}

async function main(): Promise<void> {
  const rootDir = getRootDir();
  loadEnvironment(rootDir);
  const { command, flags } = parseArgs(process.argv.slice(2));

  switch (command) {
    case 'backlog':
      await runBacklog(rootDir, flags);
      return;
    case 'daily':
      await runDaily(rootDir, flags);
      return;
    case 'stage':
      await runStage(rootDir, flags);
      return;
    default:
      console.log(
        'Usage: tsx src/cli/index.ts <backlog|daily|stage> [--workspace-root <path>] [--article-id <id>] [--min-ready-topics <n>] [--dry-run] [--run-stages] [--force-human-bypass] [--publish] [--push]'
      );
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
