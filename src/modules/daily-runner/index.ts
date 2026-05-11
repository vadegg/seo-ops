import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';
import { ensureDir, readYamlFile, writeJson, writeText } from '../../lib/files.js';
import { createRunLogger } from '../../lib/run-log.js';
import { getBacklogFiles, loadRunnerConfig, resolveWorkspacePath } from '../../lib/workspace-config.js';
import { resolveWorkspaceRoot, runBacklogAudit, type AuditResult, type TopicRecord } from '../backlog-audit/index.js';
import { runStageRunner } from '../stage-runner/index.js';

interface WorkflowConfig {
  mode?: {
    publish_to_blog?: boolean;
    require_final_human_approval?: boolean;
  };
  paths?: {
    work_items_dir?: string;
    archive_published_dir?: string;
  };
}

interface WorkQueueItem {
  article_id: string;
  topic: string;
  primary_keyword: string;
  topic_slug: string;
  work_item_path: string;
  state: string;
  current_stage: string;
  next_actor: string;
  process_mode: string;
  created_at: string;
  source_program: string;
  source_path: string;
}

interface WorkQueueFile {
  version: number;
  updated_at: string;
  items: WorkQueueItem[];
}

interface DailyRunnerOptions {
  workspaceRoot?: string;
  minReadyTopics?: number;
  dryRun?: boolean;
  runStages?: boolean;
  forceHumanBypass?: boolean;
  publish?: boolean;
  push?: boolean;
}

interface DailyRunnerResult {
  status:
    | 'created_work_item'
    | 'created_maintenance_task'
    | 'no_action_active_exists'
    | 'no_action_no_topics'
    | 'advanced_existing_work_item'
    | 'advanced_new_work_item';
  workspaceRoot: string;
  articleId?: string;
  workItemPath?: string;
  topic?: string;
  selectionMode?: 'publishable' | 'needs_evidence_ingest';
  maintenanceTaskPath?: string;
  dryRun: boolean;
  reportPath: string;
  logPath: string;
  backlogAudit: AuditResult;
}

interface StrategyEvidenceEntry {
  evidence_id?: string;
  type?: string;
  one_liner?: string;
  where?: string;
  strength?: string;
}

interface MaintenanceCandidate {
  program: string;
  sourcePath: string;
  backlogFile: string;
  reason: string;
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function toArticleDatePrefix(date: Date): string {
  const year = String(date.getUTCFullYear()).slice(-2);
  const month = String(date.getUTCMonth() + 1).padStart(2, '0');
  const day = String(date.getUTCDate()).padStart(2, '0');
  return `${year}${month}${day}`;
}

function writeYamlFile(filePath: string, data: unknown): void {
  fs.writeFileSync(filePath, yaml.dump(data, { lineWidth: 120, noRefs: true }), 'utf8');
}

function fileExists(filePath: string): boolean {
  return fs.existsSync(filePath);
}

function normalizeKey(value: string | undefined): string {
  return (value ?? '')
    .toLowerCase()
    .replace(/\.md$/i, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function loadWorkflowConfig(workspaceRoot: string): WorkflowConfig {
  return readYamlFile<WorkflowConfig>(path.join(workspaceRoot, 'workflow-config.yaml'));
}

function loadWorkQueue(workspaceRoot: string): WorkQueueFile {
  const queuePath = path.join(workspaceRoot, 'work-queue.yaml');
  if (!fileExists(queuePath)) {
    return {
      version: 1,
      updated_at: new Date().toISOString(),
      items: []
    };
  }

  const queue = readYamlFile<WorkQueueFile>(queuePath);
  return {
    version: queue.version ?? 1,
    updated_at: queue.updated_at ?? new Date().toISOString(),
    items: queue.items ?? []
  };
}

function saveWorkQueue(workspaceRoot: string, queue: WorkQueueFile): void {
  queue.updated_at = new Date().toISOString();
  writeYamlFile(path.join(workspaceRoot, 'work-queue.yaml'), queue);
}

function listArticleIds(workspaceRoot: string, workflowConfig: WorkflowConfig): Set<string> {
  const ids = new Set<string>();
  const workItemsDir = path.join(workspaceRoot, workflowConfig.paths?.work_items_dir ?? 'work-items');
  const archivePublishedDir = path.join(workspaceRoot, workflowConfig.paths?.archive_published_dir ?? 'archive/published');

  for (const dirPath of [workItemsDir, archivePublishedDir]) {
    if (!fileExists(dirPath)) {
      continue;
    }

    for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        ids.add(entry.name);
      }
    }
  }

  return ids;
}

function generateArticleId(workspaceRoot: string, workflowConfig: WorkflowConfig, topic: TopicRecord): string {
  const prefix = toArticleDatePrefix(new Date());
  const slug = slugify(topic.primaryKeyword);
  const existing = listArticleIds(workspaceRoot, workflowConfig);
  let sequence = 1;

  while (true) {
    const candidate = `${prefix}-${slug}-${sequence}`;
    if (!existing.has(candidate)) {
      return candidate;
    }
    sequence += 1;
  }
}

function loadStrategyEvidenceMap(topic: TopicRecord): Map<string, StrategyEvidenceEntry> {
  if (!fileExists(topic.sourcePath)) {
    return new Map();
  }

  const doc = readYamlFile<Record<string, any>>(topic.sourcePath);
  const map = new Map<string, StrategyEvidenceEntry>();

  for (const item of doc.evidence_bank ?? []) {
    if (item.evidence_id) {
      map.set(item.evidence_id, item);
    }
  }

  return map;
}

function buildIntake(topic: TopicRecord, articleId: string, workspaceRoot: string) {
  const evidenceMap = loadStrategyEvidenceMap(topic);
  const runnerConfig = loadRunnerConfig(workspaceRoot);
  const topicSourcePath = path.relative(workspaceRoot, topic.sourcePath);
  const evidenceRefs = topic.requiredEvidenceIds.map((id) => `${topicSourcePath}#${id}`);
  const firstPartyEvidence = topic.requiredEvidenceIds.map((id) => {
    const evidence = evidenceMap.get(id);
    return {
      type: evidence?.type ?? 'experience',
      note: evidence?.one_liner ?? `Required evidence ${id} from strategy backlog.`,
      source_path: `${topicSourcePath}#${id}`,
      usable_in_article: evidence?.strength === 'strong' || evidence?.strength === 'medium'
    };
  });

  if (firstPartyEvidence.length === 0 && topic.sourceMaterials.length > 0) {
    for (const sourceMaterial of topic.sourceMaterials) {
      firstPartyEvidence.push({
        type: 'artifact',
        note: `Source pack material for ${topic.primaryKeyword}.`,
        source_path: sourceMaterial,
        usable_in_article: true
      });
      evidenceRefs.push(sourceMaterial);
    }
  }

  const intake = {
    article_id: articleId,
    created_at: new Date().toISOString(),
    created_by: 'daily-runner',
    source: {
      type: 'strategy-backlog',
      run_id: articleId,
      source_path: topicSourcePath
    },
    topic: topic.topic,
    topic_slug: topic.topicSlug ?? slugify(topic.primaryKeyword),
    cluster: topic.cycleId,
    primary_keyword: topic.primaryKeyword,
    secondary_keywords: [],
    search_intent: 'unknown',
    icp_or_audience: '',
    article_type: 'how-to',
    desired_cta: '',
    internal_links: [],
    allowed_sources: ['First-party materials', 'Primary sources', 'Official documentation where relevant'],
    evidence_bank_refs: evidenceRefs,
    evidence_search_notes:
      topic.requiredEvidenceIds.length > 0
        ? `Daily runner selected required evidence ids: ${topic.requiredEvidenceIds.join(', ')}.`
        : `Daily runner selected source pack materials for ${topic.primaryKeyword}.`,
    first_party_evidence: firstPartyEvidence.length > 0
      ? firstPartyEvidence
      : [
          {
            type: 'experience',
            note: 'No evidence was auto-attached by daily runner.',
            source_path: '',
            usable_in_article: false
          }
        ],
    constraints: {
      language: runnerConfig.editorial?.language ?? 'en',
      geo_targets: runnerConfig.editorial?.geo_targets ?? ['US', 'EU'],
      do_not_publish_without_evidence: true
    },
    notes: `Auto-created from ${topic.program} / ${topic.cycleName}.`
  };

  return intake;
}

function applyTopicMetadataToIntake(intake: Record<string, any>, topic: TopicRecord): Record<string, any> {
  const doc = fileExists(topic.sourcePath) ? readYamlFile<Record<string, any>>(topic.sourcePath) : {};
  const candidate = findTopicSourceRecord(doc, topic);

  if (!candidate) {
    return intake;
  }

  intake.secondary_keywords = candidate.secondary_keywords ?? intake.secondary_keywords;
  intake.search_intent = candidate.search_intent ?? intake.search_intent;
  intake.icp_or_audience = candidate.icp_or_audience ?? intake.icp_or_audience;
  intake.article_type = candidate.article_type ?? intake.article_type;
  intake.desired_cta = candidate.desired_cta ?? intake.desired_cta;
  intake.internal_links = candidate.internal_links_candidates ?? intake.internal_links;
  intake.allowed_sources = candidate.allowed_sources ?? intake.allowed_sources;

  return intake;
}

function findTopicSourceRecord(doc: Record<string, any>, topic: TopicRecord): Record<string, any> | null {
  for (const cycle of doc.cycles ?? []) {
    if (cycle.cycle_id === topic.cycleId) {
      const found = (cycle.topics ?? []).find((item: Record<string, any>) => item.primary_keyword === topic.primaryKeyword);
      if (found) {
        return found;
      }
    }
  }

  for (const article of doc.candidate_articles ?? []) {
    if (article.primary_keyword === topic.primaryKeyword) {
      return article;
    }
  }

  for (const section of Object.values(doc.later_stage_topics ?? {}) as Array<Record<string, any>>) {
    const found = (section.topics ?? []).find((item: Record<string, any>) => item.primary_keyword === topic.primaryKeyword);
    if (found) {
      return found;
    }
  }

  return null;
}

function buildStatus(workflowConfig: WorkflowConfig, articleId: string, topic: TopicRecord) {
  return {
    article_id: articleId,
    state: 'queued',
    current_stage: 'intake',
    current_owner: 'daily-runner',
    next_actor: '1-research-agent',
    latest_artifact: 'intake.yaml',
    latest_approved_artifact: 'intake.yaml',
    approvals: {
      topic_gate: 'pending',
      outline: 'pending',
      final: 'pending'
    },
    process_mode: topic.recommendedProcess ?? '',
    publish: {
      publish_to_blog: Boolean(workflowConfig.mode?.publish_to_blog),
      require_final_human_approval: workflowConfig.mode?.require_final_human_approval ?? true
    },
    blocking_issues: [],
    cleanup: {
      status: 'active'
    }
  };
}

function buildIntakeNotes(topic: TopicRecord, articleId: string, backlogAudit: AuditResult): string {
  const lines = [
    '# Intake Notes',
    '',
    `- article_id: \`${articleId}\``,
    `- selected_topic: \`${topic.topic}\``,
    `- primary_keyword: \`${topic.primaryKeyword}\``,
    `- cycle: \`${topic.cycleName}\``,
    `- source_program: \`${topic.program}\``,
    `- process_mode: \`${topic.recommendedProcess ?? 'n/a'}\``,
    `- backlog_ready_count_at_selection: \`${backlogAudit.readyCount}\``,
    `- backlog_replenish_now_count_at_selection: \`${backlogAudit.replenishNowCount}\``,
    `- reason_selected: ${topic.reason}`,
    '',
    '## Next step',
    '',
    'Research agent should pick up this work-item from intake and produce `1-research/research-report.md`.'
  ];

  return `${lines.join('\n')}\n`;
}

function ensureWorkItemSkeleton(workItemDir: string): void {
  ensureDir(workItemDir);
  ['1-research', '2-strategy', '3-writing', '4-editing', '5-publish'].forEach((dirName) => {
    ensureDir(path.join(workItemDir, dirName));
  });
}

function createWorkItem(workspaceRoot: string, workflowConfig: WorkflowConfig, topic: TopicRecord, backlogAudit: AuditResult, dryRun = false) {
  const articleId = generateArticleId(workspaceRoot, workflowConfig, topic);
  const workItemsDir = workflowConfig.paths?.work_items_dir ?? 'work-items';
  const workItemPath = path.join(workItemsDir, articleId);
  const workItemDir = path.join(workspaceRoot, workItemPath);
  const intake = applyTopicMetadataToIntake(buildIntake(topic, articleId, workspaceRoot), topic);
  const status = buildStatus(workflowConfig, articleId, topic);
  const intakeNotes = buildIntakeNotes(topic, articleId, backlogAudit);

  if (!dryRun) {
    ensureWorkItemSkeleton(workItemDir);
    writeText(path.join(workItemDir, 'intake-notes.md'), intakeNotes);
    writeYamlFile(path.join(workItemDir, 'intake.yaml'), intake);
    writeYamlFile(path.join(workItemDir, 'status.yaml'), status);
  }

  return { articleId, workItemPath, intake, status };
}

function updateQueueForWorkItem(workspaceRoot: string, topic: TopicRecord, articleId: string, workItemPath: string, dryRun = false): WorkQueueFile {
  const queue = loadWorkQueue(workspaceRoot);
  queue.items.push({
    article_id: articleId,
    topic: topic.topic,
    primary_keyword: topic.primaryKeyword,
    topic_slug: topic.topicSlug ?? slugify(topic.primaryKeyword),
    work_item_path: workItemPath,
    state: 'queued',
    current_stage: 'intake',
    next_actor: '1-research-agent',
    process_mode: topic.recommendedProcess ?? '',
    created_at: new Date().toISOString(),
    source_program: topic.program,
    source_path: path.relative(workspaceRoot, topic.sourcePath)
  });

  if (!dryRun) {
    saveWorkQueue(workspaceRoot, queue);
  }

  return queue;
}

function collectIndexedSourcePaths(workspaceRoot: string): Set<string> {
  const runnerConfig = loadRunnerConfig(workspaceRoot);
  const evidenceIndexPath = resolveWorkspacePath(
    workspaceRoot,
    runnerConfig.evidence_index_file,
    'evidence-bank/evidence-index.yaml'
  );
  const indexed = new Set<string>();

  if (!fileExists(evidenceIndexPath)) {
    return indexed;
  }

  const evidenceIndex = readYamlFile<Record<string, any>>(evidenceIndexPath);
  for (const item of evidenceIndex.items ?? []) {
    if (typeof item?.source_path === 'string' && item.source_path.length > 0) {
      indexed.add(normalizeKey(item.source_path));
    }
  }

  return indexed;
}

function isPotentialEvidenceSource(sourcePath: string): boolean {
  return (
    sourcePath.startsWith('research/raw/') ||
    sourcePath.startsWith('evidence-bank/inbox/') ||
    sourcePath.startsWith('Archive/Research')
  );
}

function collectMaintenanceCandidatesForNeedsIngest(workspaceRoot: string): MaintenanceCandidate[] {
  const runnerConfig = loadRunnerConfig(workspaceRoot);
  const indexedSourcePaths = collectIndexedSourcePaths(workspaceRoot);
  const seen = new Set<string>();
  const candidates: MaintenanceCandidate[] = [];

  for (const backlogFile of getBacklogFiles(workspaceRoot, runnerConfig)) {
    const doc = readYamlFile<Record<string, any>>(backlogFile);
    for (const sourcePath of doc.source_documents ?? []) {
      if (typeof sourcePath !== 'string' || !isPotentialEvidenceSource(sourcePath)) {
        continue;
      }

      const absolutePath = path.join(workspaceRoot, sourcePath);
      if (!fileExists(absolutePath)) {
        continue;
      }

      const normalizedPath = normalizeKey(sourcePath);
      if (!normalizedPath || indexedSourcePaths.has(normalizedPath) || seen.has(normalizedPath)) {
        continue;
      }

      seen.add(normalizedPath);
      candidates.push({
        program: String(doc.program ?? path.basename(backlogFile, path.extname(backlogFile))),
        sourcePath,
        backlogFile: path.relative(workspaceRoot, backlogFile),
        reason: 'Raw source document exists but is not represented in evidence-bank/evidence-index.yaml.'
      });
    }
  }

  return candidates.sort((left, right) => left.sourcePath.localeCompare(right.sourcePath));
}

function writeMaintenanceTaskReport(workspaceRoot: string, candidates: MaintenanceCandidate[], dryRun: boolean): string {
  const reportsDir = path.join(workspaceRoot, 'reports/maintenance');
  ensureDir(reportsDir);

  const lines = [
    '# Needs-Ingest Replenishment Task',
    '',
    `Generated at: ${new Date().toISOString()}`,
    `Dry run: ${dryRun ? 'true' : 'false'}`,
    `Candidate source documents: ${candidates.length}`,
    '',
    '## Task',
    '',
    'Ingest the source documents below into `evidence-bank/evidence-index.yaml` so they can repopulate `needs_evidence_ingest` candidates.',
    ''
  ];

  for (const candidate of candidates) {
    lines.push(`- source_path: \`${candidate.sourcePath}\``);
    lines.push(`  program: ${candidate.program}`);
    lines.push(`  backlog_file: ${candidate.backlogFile}`);
    lines.push(`  reason: ${candidate.reason}`);
  }

  const latestPath = path.join(reportsDir, 'needs-ingest-replenishment-latest.md');
  writeText(latestPath, `${lines.join('\n')}\n`);
  writeJson(path.join(reportsDir, 'needs-ingest-replenishment-latest.json'), {
    generatedAt: new Date().toISOString(),
    dryRun,
    candidates
  });
  return latestPath;
}

function sortTopicsForPickup(topics: TopicRecord[]): TopicRecord[] {
  return [...topics].sort((left, right) => {
    const leftOrder = left.launchOrder ?? Number.MAX_SAFE_INTEGER;
    const rightOrder = right.launchOrder ?? Number.MAX_SAFE_INTEGER;
    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }

    return left.topic.localeCompare(right.topic);
  });
}

function selectTopicForDailyRun(backlogAudit: AuditResult): { topic?: TopicRecord; selectionMode?: 'publishable' | 'needs_evidence_ingest' } {
  const publishable = sortTopicsForPickup(backlogAudit.nextPublishable)[0];
  if (publishable) {
    return { topic: publishable, selectionMode: 'publishable' };
  }

  const replenishFallback = sortTopicsForPickup(backlogAudit.needsEvidenceIngest)[0];
  if (replenishFallback) {
    return { topic: replenishFallback, selectionMode: 'needs_evidence_ingest' };
  }

  return {};
}

function writeDailyReport(workspaceRoot: string, result: DailyRunnerResult): string {
  const lines = [
    '# Daily Runner Report',
    '',
    `Generated at: ${new Date().toISOString()}`,
    `Status: ${result.status}`,
    `Dry run: ${result.dryRun ? 'true' : 'false'}`,
    `Backlog ready count: ${result.backlogAudit.readyCount}`,
    `Backlog replenish-now count: ${result.backlogAudit.replenishNowCount}`,
    `Needs evidence ingest: ${result.backlogAudit.needsIngestCount}`,
    ''
  ];

  if (result.articleId && result.topic && result.workItemPath) {
    lines.push('## Created Work Item', '');
    lines.push(`- article_id: \`${result.articleId}\``);
    lines.push(`- topic: \`${result.topic}\``);
    lines.push(`- work_item_path: \`${result.workItemPath}\``);
    if (result.selectionMode === 'needs_evidence_ingest') {
      lines.push('- selection_mode: `needs_evidence_ingest`');
      lines.push('- note: publishable queue was empty, so daily runner pulled the next source-pack-backed backlog candidate.');
    }
    if (result.dryRun) {
      lines.push('- note: dry-run only, files and queue were not written.');
    }
    lines.push('');
  } else {
    lines.push('## Outcome', '');
    lines.push('- No new work item was created.');
    if (result.maintenanceTaskPath) {
      lines.push(`- Maintenance task created: \`${path.relative(workspaceRoot, result.maintenanceTaskPath)}\``);
    }
    lines.push('');
  }

  lines.push('## Next Publishable Topics', '');
  for (const topic of result.backlogAudit.nextPublishable.slice(0, 5)) {
    lines.push(`- \`${topic.primaryKeyword}\` — ${topic.reason}`);
  }
  lines.push('');

  const reportPath = path.join(workspaceRoot, 'reports/status/daily-runner-latest.md');
  writeText(reportPath, `${lines.join('\n')}\n`);
  writeJson(path.join(workspaceRoot, 'reports/status/daily-runner-latest.json'), result);
  return reportPath;
}

export async function runDailyRunner(currentDir: string, options?: DailyRunnerOptions): Promise<DailyRunnerResult> {
  const workspaceRoot = resolveWorkspaceRoot(currentDir, options?.workspaceRoot);
  const workflowConfig = loadWorkflowConfig(workspaceRoot);
  const runLog = createRunLogger('keyword:agent:daily', workspaceRoot);

  try {
    runLog.addStep('Start daily runner', 'Running backlog audit and deciding whether a new work-item should be created today.');
    const backlogAudit = runBacklogAudit(currentDir, {
      workspaceRoot,
      minReadyTopics: options?.minReadyTopics
    });

    runLog.addResult(`Ready topics: ${backlogAudit.readyCount}`);
    runLog.addResult(`Replenish-now topics: ${backlogAudit.replenishNowCount}`);
    runLog.addResult(`Needs evidence ingest: ${backlogAudit.needsIngestCount}`);

    if (backlogAudit.activeCount > 0) {
      if (options?.runStages) {
        runLog.addStep('Advance active work item', 'An active work item exists, so daily runner will try to move it through its next stages.');
        const stageResult = await runStageRunner(currentDir, {
          workspaceRoot: options?.workspaceRoot,
          dryRun: options?.dryRun,
          forceHumanBypass: options?.forceHumanBypass,
          publish: options?.publish,
          push: options?.push
        });
        runLog.addResult(`Stage runner status: ${stageResult.status}`);
        const provisional: DailyRunnerResult = {
          status: 'advanced_existing_work_item',
          workspaceRoot,
          articleId: stageResult.articleId,
          workItemPath: stageResult.workItemPath,
          dryRun: Boolean(options?.dryRun),
          backlogAudit,
          reportPath: '',
          logPath: ''
        };
        const reportPath = writeDailyReport(workspaceRoot, provisional);
        const logPath = runLog.complete(`Advanced existing work item ${stageResult.articleId} via stage runner.`);
        const result = { ...provisional, reportPath, logPath };
        writeJson(path.join(workspaceRoot, 'reports/status/daily-runner-latest.json'), result);
        return result;
      }

      runLog.addBlocker('Active work items already exist in the queue, so daily runner did not create another one.');
      const provisional: DailyRunnerResult = {
        status: 'no_action_active_exists',
        workspaceRoot,
        dryRun: Boolean(options?.dryRun),
        backlogAudit,
        reportPath: '',
        logPath: ''
      };
      const reportPath = writeDailyReport(workspaceRoot, provisional);
      const logPath = runLog.complete('Skipped new intake because at least one active work-item already exists.');
      const result = { ...provisional, reportPath, logPath };
      writeJson(path.join(workspaceRoot, 'reports/status/daily-runner-latest.json'), result);
      return result;
    }

    const { topic, selectionMode } = selectTopicForDailyRun(backlogAudit);
    if (!topic) {
      const maintenanceCandidates = collectMaintenanceCandidatesForNeedsIngest(workspaceRoot);
      if (maintenanceCandidates.length > 0) {
        runLog.addStep(
          'Replenish needs_evidence_ingest',
          'Publishable and ingest-ready queues are empty, so daily runner is creating a maintenance task from unindexed raw source documents.'
        );
        const maintenanceTaskPath = writeMaintenanceTaskReport(
          workspaceRoot,
          maintenanceCandidates,
          Boolean(options?.dryRun)
        );
        runLog.addResult(`Maintenance candidates: ${maintenanceCandidates.length}`);
        const provisional: DailyRunnerResult = {
          status: 'created_maintenance_task',
          workspaceRoot,
          dryRun: Boolean(options?.dryRun),
          backlogAudit,
          maintenanceTaskPath,
          reportPath: '',
          logPath: ''
        };
        const reportPath = writeDailyReport(workspaceRoot, provisional);
        const logPath = runLog.complete('Created maintenance task to replenish needs_evidence_ingest.');
        const result = { ...provisional, reportPath, logPath };
        writeJson(path.join(workspaceRoot, 'reports/status/daily-runner-latest.json'), result);
        return result;
      }

      runLog.addBlocker('No publishable topics, source-pack-backed replenishment candidates, or unindexed source documents are available in the backlog.');
      const provisional: DailyRunnerResult = {
        status: 'no_action_no_topics',
        workspaceRoot,
        dryRun: Boolean(options?.dryRun),
        backlogAudit,
        reportPath: '',
        logPath: ''
      };
      const reportPath = writeDailyReport(workspaceRoot, provisional);
      const logPath = runLog.complete('Skipped new intake because no publishable topic is available.');
      const result = { ...provisional, reportPath, logPath };
      writeJson(path.join(workspaceRoot, 'reports/status/daily-runner-latest.json'), result);
      return result;
    }

    if (selectionMode === 'needs_evidence_ingest') {
      runLog.addStep(
        'Replenish backlog upstream',
        'Publishable queue is empty, so daily runner is promoting the next source-pack-backed candidate before evidence-bank ingest is complete.'
      );
    }
    runLog.addStep('Select topic', `Selected \`${topic.primaryKeyword}\` from ${topic.program} / ${topic.cycleName}.`);
    const { articleId, workItemPath } = createWorkItem(
      workspaceRoot,
      workflowConfig,
      topic,
      backlogAudit,
      options?.dryRun ?? false
    );
    updateQueueForWorkItem(workspaceRoot, topic, articleId, workItemPath, options?.dryRun ?? false);

    runLog.addStep('Create work item', `Created ${workItemPath} with intake, status, and stage directories.`);
    runLog.addResult(`Created article_id: ${articleId}`);
    runLog.addResult(`Selected topic: ${topic.topic}`);
    runLog.addResult(`Work item path: ${workItemPath}`);

    if (!backlogAudit.healthy) {
      runLog.addBlocker(
        `Backlog buffer remains below target after today's pickup: ${backlogAudit.readyCount}/${backlogAudit.minReadyTopics} ready topics before creation.`
      );
    }
    if (backlogAudit.needsIngestCount > 0) {
      runLog.addBlocker(
        `${backlogAudit.needsIngestCount} replenish candidates still need evidence-bank ingest.`
      );
    }

    const shouldRunStages = Boolean(options?.runStages) && !Boolean(options?.dryRun);

    const provisional: DailyRunnerResult = {
      status: shouldRunStages ? 'advanced_new_work_item' : 'created_work_item',
      workspaceRoot,
      articleId,
      workItemPath,
      topic: topic.topic,
      selectionMode,
      dryRun: Boolean(options?.dryRun),
      backlogAudit,
      reportPath: '',
      logPath: ''
    };

    if (options?.runStages && options?.dryRun) {
      runLog.addStep(
        'Skip stage runner in dry-run mode',
        `Dry-run did not persist ${articleId}, so stage advancement was intentionally skipped.`
      );
    }

    if (shouldRunStages) {
      runLog.addStep('Advance new work item', `Running stage runner for ${articleId}.`);
      const stageResult = await runStageRunner(currentDir, {
        workspaceRoot: options?.workspaceRoot,
        articleId,
        dryRun: options?.dryRun,
        forceHumanBypass: options?.forceHumanBypass,
        publish: options?.publish,
        push: options?.push
      });
      runLog.addResult(`Stage runner status: ${stageResult.status}`);
    }

    const reportPath = writeDailyReport(workspaceRoot, provisional);
    const logPath = runLog.complete(`Created daily work-item ${articleId} for ${topic.primaryKeyword}.`);
    const result = { ...provisional, reportPath, logPath };
    writeJson(path.join(workspaceRoot, 'reports/status/daily-runner-latest.json'), result);
    return result;
  } catch (error) {
    const logPath = runLog.fail(error, 'Daily runner failed before finishing the daily planning cycle.');
    const fallback: DailyRunnerResult = {
      status: 'no_action_no_topics',
      workspaceRoot,
      dryRun: Boolean(options?.dryRun),
      backlogAudit: runBacklogAudit(currentDir, {
        workspaceRoot,
        minReadyTopics: options?.minReadyTopics
      }),
      reportPath: path.join(workspaceRoot, 'reports/status/daily-runner-latest.md'),
      logPath
    };
    throw error;
  }
}
