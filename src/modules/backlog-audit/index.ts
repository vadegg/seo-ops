import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';
import { ensureDir, readYamlFile, writeJson, writeText } from '../../lib/files.js';
import { getBacklogFiles, loadRunnerConfig, resolveWorkspacePath } from '../../lib/workspace-config.js';

export type TopicStatus =
  | 'published'
  | 'active'
  | 'ready'
  | 'queued'
  | 'replenish_now'
  | 'needs_evidence_ingest'
  | 'blocked_by_sequence'
  | 'parked'
  | 'unknown';

interface WorkflowConfig {
  paths?: {
    blog_content_dir?: string;
    archive_published_dir?: string;
  };
}

interface WorkQueueFile {
  items?: Array<{
    article_id?: string;
    topic?: string;
    primary_keyword?: string;
    slug?: string;
    work_item_path?: string;
  }>;
}

interface EvidenceIndexFile {
  items?: Array<{
    source_path?: string;
    usable_for?: {
      topics?: string[];
    };
    extractable_evidence?: Array<{
      suggested_keywords?: string[];
      strength?: string;
    }>;
  }>;
}

export interface TopicRecord {
  program: string;
  cycleId: string;
  cycleName: string;
  launchOrder?: number;
  topic: string;
  primaryKeyword: string;
  topicSlug?: string;
  recommendedProcess?: string;
  sourceType: 'market' | 'ai';
  sourcePath: string;
  rawStatus: string;
  effectiveStatus: TopicStatus;
  reason: string;
  requiredEvidenceIds: string[];
  sourceMaterials: string[];
}

export interface AuditResult {
  generatedAt: string;
  workspaceRoot: string;
  minReadyTopics: number;
  readyCount: number;
  replenishNowCount: number;
  activeCount: number;
  publishedCount: number;
  needsIngestCount: number;
  healthy: boolean;
  nextPublishable: TopicRecord[];
  replenishNow: TopicRecord[];
  needsEvidenceIngest: TopicRecord[];
  active: TopicRecord[];
  published: TopicRecord[];
  blocked: TopicRecord[];
}

function normalizeKey(value: string | undefined): string {
  return (value ?? '')
    .toLowerCase()
    .replace(/\.md$/i, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function normalizeSlug(value: string | undefined): string {
  return (value ?? '')
    .toLowerCase()
    .replace(/\.md$/i, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function fileExists(filePath: string): boolean {
  return fs.existsSync(filePath);
}

function listFilesRecursively(dirPath: string): string[] {
  if (!fileExists(dirPath)) {
    return [];
  }

  const output: string[] = [];
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });

  for (const entry of entries) {
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      output.push(...listFilesRecursively(fullPath));
      continue;
    }

    output.push(fullPath);
  }

  return output;
}

function readMarkdownFrontmatter(filePath: string): Record<string, unknown> | null {
  const content = fs.readFileSync(filePath, 'utf8');
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) {
    return null;
  }

  return (yaml.load(match[1]) as Record<string, unknown>) ?? null;
}

function addNormalizedKeys(keys: Set<string>, values: Array<string | undefined>): void {
  for (const value of values) {
    const normalized = normalizeKey(value);
    if (normalized) {
      keys.add(normalized);
    }
  }
}

function extractStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === 'string');
}

function collectPublishedKeys(workspaceRoot: string, workflowConfig: WorkflowConfig): Set<string> {
  const keys = new Set<string>();
  const archiveDir = resolveWorkspacePath(
    workspaceRoot,
    workflowConfig.paths?.archive_published_dir,
    'archive/published'
  );
  const blogDir = resolveWorkspacePath(
    workspaceRoot,
    workflowConfig.paths?.blog_content_dir,
    'blog/src/content/blog'
  );

  for (const frontmatterPath of listFilesRecursively(archiveDir).filter((filePath) => filePath.endsWith('frontmatter.yaml'))) {
    const frontmatter = readYamlFile<Record<string, unknown>>(frontmatterPath);
    const slug = typeof frontmatter.slug === 'string' ? frontmatter.slug : '';
    const title = typeof frontmatter.title === 'string' ? frontmatter.title : '';
    const primaryKeyword = typeof frontmatter.primary_keyword === 'string' ? frontmatter.primary_keyword : '';
    const tags = extractStringArray(frontmatter.tags);

    addNormalizedKeys(keys, [slug, title, primaryKeyword, ...tags]);
  }

  for (const markdownPath of listFilesRecursively(blogDir).filter((filePath) => filePath.endsWith('.md'))) {
    const frontmatter = readMarkdownFrontmatter(markdownPath);
    const slug = typeof frontmatter?.slug === 'string' ? frontmatter.slug : path.basename(markdownPath, '.md');
    const title = typeof frontmatter?.title === 'string' ? frontmatter.title : '';
    const primaryKeyword = typeof frontmatter?.primary_keyword === 'string' ? frontmatter.primary_keyword : '';
    const tags = extractStringArray(frontmatter?.tags);

    addNormalizedKeys(keys, [slug, title, primaryKeyword, path.basename(markdownPath, '.md'), ...tags]);
  }

  return keys;
}

function collectActiveKeys(workspaceRoot: string): Set<string> {
  const keys = new Set<string>();
  const workQueuePath = path.join(workspaceRoot, 'work-queue.yaml');
  if (!fileExists(workQueuePath)) {
    return keys;
  }

  const queue = readYamlFile<WorkQueueFile>(workQueuePath);
  for (const item of queue.items ?? []) {
    [item.topic, item.primary_keyword, item.slug, item.article_id].forEach((value) => {
      const normalized = normalizeKey(value);
      if (normalized) {
        keys.add(normalized);
      }
    });

    if (item.work_item_path) {
      const intakePath = path.join(workspaceRoot, item.work_item_path, 'intake.yaml');
      if (fileExists(intakePath)) {
        const intake = readYamlFile<Record<string, unknown>>(intakePath);
        [intake.topic, intake.primary_keyword, intake.topic_slug].forEach((value) => {
          const normalized = normalizeKey(typeof value === 'string' ? value : '');
          if (normalized) {
            keys.add(normalized);
          }
        });
      }
    }
  }

  return keys;
}

function collectIngestedSourcePaths(workspaceRoot: string): Set<string> {
  const runnerConfig = loadRunnerConfig(workspaceRoot);
  const evidenceIndexPath = resolveWorkspacePath(
    workspaceRoot,
    runnerConfig.evidence_index_file,
    'evidence-bank/evidence-index.yaml'
  );
  const ingested = new Set<string>();

  if (!fileExists(evidenceIndexPath)) {
    return ingested;
  }

  const evidenceIndex = readYamlFile<EvidenceIndexFile>(evidenceIndexPath);
  for (const item of evidenceIndex.items ?? []) {
    if (item.source_path) {
      ingested.add(normalizeKey(item.source_path));
    }
  }

  return ingested;
}

function hasStrongEvidenceForKeyword(evidenceIndex: EvidenceIndexFile, primaryKeyword: string): boolean {
  const normalizedKeyword = normalizeKey(primaryKeyword);

  return (evidenceIndex.items ?? []).some((item) => {
    const topicMatch = (item.usable_for?.topics ?? []).some((topic) => normalizeKey(topic) === normalizedKeyword);
    const keywordMatch = (item.extractable_evidence ?? []).some((evidence) =>
      (evidence.suggested_keywords ?? []).some((keyword) => normalizeKey(keyword) === normalizedKeyword) &&
      evidence.strength === 'strong'
    );
    return topicMatch || keywordMatch;
  });
}

function buildTopicsFromDocument(sourcePath: string): TopicRecord[] {
  const doc = readYamlFile<Record<string, any>>(sourcePath);
  const topics: TopicRecord[] = [];

  for (const cycle of doc.cycles ?? []) {
    for (const topic of cycle.topics ?? []) {
      topics.push({
        program: doc.program ?? path.basename(sourcePath, path.extname(sourcePath)),
        cycleId: cycle.cycle_id ?? 'unknown',
        cycleName: cycle.name ?? 'Unknown cycle',
        launchOrder: topic.launch_order,
        topic: topic.topic,
        primaryKeyword: topic.primary_keyword,
        topicSlug: topic.topic_slug,
        recommendedProcess: topic.recommended_process,
        sourceType: 'market',
        sourcePath,
        rawStatus: topic.status ?? 'unknown',
        effectiveStatus: 'unknown',
        reason: '',
        requiredEvidenceIds: topic.required_evidence_ids ?? [],
        sourceMaterials: []
      });
    }
  }

  for (const section of Object.values(doc.later_stage_topics ?? {}) as Array<Record<string, any>>) {
    for (const topic of section.topics ?? []) {
      topics.push({
        program: doc.program ?? path.basename(sourcePath, path.extname(sourcePath)),
        cycleId: 'later-stage',
        cycleName: 'Later stage topics',
        topic: topic.topic,
        primaryKeyword: topic.primary_keyword,
        recommendedProcess: topic.recommended_process,
        sourceType: 'market',
        sourcePath,
        rawStatus: topic.status ?? section.status ?? 'blocked_by_sequence',
        effectiveStatus: 'unknown',
        reason: '',
        requiredEvidenceIds: topic.required_evidence_ids ?? [],
        sourceMaterials: []
      });
    }
  }

  const cycleNameById = new Map<string, string>(
    (doc.cycles ?? []).map((cycle: Record<string, any>) => [cycle.cycle_id, cycle.name ?? cycle.cycle_id])
  );
  for (const topic of doc.candidate_articles ?? []) {
    topics.push({
      program: doc.program ?? path.basename(sourcePath, path.extname(sourcePath)),
      cycleId: topic.cycle_id ?? 'unknown',
      cycleName: cycleNameById.get(topic.cycle_id) ?? 'Unknown cycle',
      topic: topic.topic,
      primaryKeyword: topic.primary_keyword,
      recommendedProcess: topic.process_mode,
      sourceType: 'ai',
      sourcePath,
      rawStatus: topic.operational_status ?? 'unknown',
      effectiveStatus: 'unknown',
      reason: '',
      requiredEvidenceIds: [],
      sourceMaterials: topic.source_materials ?? []
    });
  }

  return topics;
}

function classifyTopic(
  topic: TopicRecord,
  publishedKeys: Set<string>,
  activeKeys: Set<string>,
  ingestedSourcePaths: Set<string>,
  evidenceIndex: EvidenceIndexFile
): TopicRecord {
  const keys = [topic.primaryKeyword, topic.topic, topic.topicSlug].map((value) => normalizeKey(value));

  if (keys.some((key) => key && publishedKeys.has(key))) {
    return { ...topic, effectiveStatus: 'published', reason: 'Already published in the blog or archive.' };
  }

  if (keys.some((key) => key && activeKeys.has(key))) {
    return { ...topic, effectiveStatus: 'active', reason: 'Already present in the active work queue.' };
  }

  if (topic.sourceType === 'market') {
    if (topic.rawStatus === 'ready') {
      return { ...topic, effectiveStatus: 'ready', reason: 'Already marked ready in the evidence-qualified backlog.' };
    }

    if (topic.rawStatus === 'queued') {
      return { ...topic, effectiveStatus: 'replenish_now', reason: 'Evidence is defined and the topic is queued for the next open publishing slot.' };
    }

    if (topic.rawStatus === 'blocked_by_sequence') {
      return { ...topic, effectiveStatus: 'blocked_by_sequence', reason: 'Held back by sequence rules in the strategy backlog.' };
    }

    return { ...topic, effectiveStatus: 'unknown', reason: `Unhandled market backlog status: ${topic.rawStatus}.` };
  }

  const allSourceMaterialsExist = topic.sourceMaterials.every((sourceMaterial) =>
    fileExists(path.join(path.dirname(path.dirname(topic.sourcePath)), sourceMaterial))
  );
  const anySourceMaterialIngested = topic.sourceMaterials.some((sourceMaterial) =>
    ingestedSourcePaths.has(normalizeKey(sourceMaterial))
  );
  const keywordHasEvidence = hasStrongEvidenceForKeyword(evidenceIndex, topic.primaryKeyword);

  if ((anySourceMaterialIngested || keywordHasEvidence) && allSourceMaterialsExist) {
    return {
      ...topic,
      effectiveStatus: 'replenish_now',
      reason: 'Source pack exists and the topic already has evidence coverage in the evidence index.'
    };
  }

  if (allSourceMaterialsExist) {
    return {
      ...topic,
      effectiveStatus: 'needs_evidence_ingest',
      reason: 'Source pack exists, but it still needs to be ingested into the evidence bank before activation.'
    };
  }

  return {
    ...topic,
    effectiveStatus: 'parked',
    reason: 'Source materials are incomplete, so the topic cannot replenish the backlog yet.'
  };
}

function renderMarkdownReport(result: AuditResult): string {
  const lines: string[] = [];

  lines.push('# Evidence-qualified backlog audit');
  lines.push('');
  lines.push(`Generated at: ${result.generatedAt}`);
  lines.push(`Minimum ready buffer: ${result.minReadyTopics}`);
  lines.push(`Ready now: ${result.readyCount}`);
  lines.push(`Can replenish now: ${result.replenishNowCount}`);
  lines.push(`Needs evidence ingest: ${result.needsIngestCount}`);
  lines.push(`Active work items: ${result.activeCount}`);
  lines.push(`Published topics: ${result.publishedCount}`);
  lines.push(`Backlog health: ${result.healthy ? 'healthy' : 'needs replenishment'}`);
  lines.push('');

  const sections: Array<[string, TopicRecord[]]> = [
    ['Next publishable topics', result.nextPublishable],
    ['Replenish now', result.replenishNow],
    ['Needs evidence ingest', result.needsEvidenceIngest],
    ['Blocked or parked', result.blocked]
  ];

  for (const [title, topics] of sections) {
    lines.push(`## ${title}`);
    lines.push('');

    if (topics.length === 0) {
      lines.push('_None_');
      lines.push('');
      continue;
    }

    for (const topic of topics) {
      lines.push(`- ${topic.primaryKeyword} (${topic.program}, ${topic.effectiveStatus})`);
      lines.push(`  topic: ${topic.topic}`);
      lines.push(`  cycle: ${topic.cycleName}`);
      lines.push(`  process: ${topic.recommendedProcess ?? 'n/a'}`);
      lines.push(`  reason: ${topic.reason}`);
    }

    lines.push('');
  }

  return `${lines.join('\n').trim()}\n`;
}

export function resolveWorkspaceRoot(currentDir: string, explicitWorkspaceRoot?: string): string {
  if (explicitWorkspaceRoot) {
    return path.resolve(currentDir, explicitWorkspaceRoot);
  }

  if (process.env.SEO_WORKSPACE_ROOT) {
    return path.resolve(currentDir, process.env.SEO_WORKSPACE_ROOT);
  }

  const localWorkflowConfig = path.join(currentDir, 'workflow-config.yaml');
  if (fileExists(localWorkflowConfig)) {
    return currentDir;
  }

  throw new Error(
    'Workspace root is not set. Pass --workspace-root <path> or define SEO_WORKSPACE_ROOT in the runner .env file.'
  );
}

export function runBacklogAudit(currentDir: string, options?: { workspaceRoot?: string; minReadyTopics?: number }): AuditResult {
  const workspaceRoot = resolveWorkspaceRoot(currentDir, options?.workspaceRoot);
  const workflowConfig = readYamlFile<WorkflowConfig>(path.join(workspaceRoot, 'workflow-config.yaml'));
  const runnerConfig = loadRunnerConfig(workspaceRoot);
  const evidenceIndex = readYamlFile<EvidenceIndexFile>(
    resolveWorkspacePath(workspaceRoot, runnerConfig.evidence_index_file, 'evidence-bank/evidence-index.yaml')
  );
  const publishedKeys = collectPublishedKeys(workspaceRoot, workflowConfig);
  const activeKeys = collectActiveKeys(workspaceRoot);
  const ingestedSourcePaths = collectIngestedSourcePaths(workspaceRoot);
  const minReadyTopics = options?.minReadyTopics ?? 7;

  const topics = getBacklogFiles(workspaceRoot, runnerConfig)
    .flatMap((sourcePath) => buildTopicsFromDocument(sourcePath))
    .map((topic) =>
    classifyTopic(topic, publishedKeys, activeKeys, ingestedSourcePaths, evidenceIndex)
  );

  const ready = topics.filter((topic) => topic.effectiveStatus === 'ready');
  const replenishNow = topics.filter((topic) => topic.effectiveStatus === 'replenish_now');
  const needsEvidenceIngest = topics.filter((topic) => topic.effectiveStatus === 'needs_evidence_ingest');
  const active = topics.filter((topic) => topic.effectiveStatus === 'active');
  const published = topics.filter((topic) => topic.effectiveStatus === 'published');
  const blocked = topics.filter((topic) =>
    ['blocked_by_sequence', 'parked', 'unknown', 'queued'].includes(topic.effectiveStatus)
  );

  const nextPublishable = [...ready, ...replenishNow]
    .sort((left, right) => {
      const leftOrder = left.launchOrder ?? Number.MAX_SAFE_INTEGER;
      const rightOrder = right.launchOrder ?? Number.MAX_SAFE_INTEGER;
      if (leftOrder !== rightOrder) {
        return leftOrder - rightOrder;
      }

      return left.topic.localeCompare(right.topic);
    })
    .slice(0, minReadyTopics);

  const result: AuditResult = {
    generatedAt: new Date().toISOString(),
    workspaceRoot,
    minReadyTopics,
    readyCount: ready.length,
    replenishNowCount: replenishNow.length,
    activeCount: active.length,
    publishedCount: published.length,
    needsIngestCount: needsEvidenceIngest.length,
    healthy: ready.length >= minReadyTopics,
    nextPublishable,
    replenishNow,
    needsEvidenceIngest,
    active,
    published,
    blocked
  };

  const reportDir = path.join(workspaceRoot, 'reports/status');
  ensureDir(reportDir);
  writeJson(path.join(reportDir, 'backlog-audit.json'), result);
  writeText(path.join(reportDir, 'backlog-audit.md'), renderMarkdownReport(result));

  return result;
}
