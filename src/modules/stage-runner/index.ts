import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import yaml from 'js-yaml';
import { ensureDir, readYamlFile, writeJson, writeText } from '../../lib/files.js';
import { generateText } from '../../lib/llm.js';
import { createRunLogger } from '../../lib/run-log.js';
import { resolveWorkspaceRoot } from '../backlog-audit/index.js';

interface WorkflowConfig {
  mode?: {
    publish_to_blog?: boolean;
    require_final_human_approval?: boolean;
  };
  paths?: {
    work_items_dir?: string;
    archive_published_dir?: string;
    archive_dropped_dir?: string;
    blog_content_dir?: string;
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

interface StageRunnerOptions {
  workspaceRoot?: string;
  articleId?: string;
  dryRun?: boolean;
  forceHumanBypass?: boolean;
  publish?: boolean;
  push?: boolean;
}

interface StageRunnerResult {
  status: 'completed' | 'stopped';
  articleId: string;
  finalStage: string;
  reason: string;
  reportPath: string;
  logPath: string;
  workItemPath: string;
}

interface StatusFile {
  article_id: string;
  state: string;
  current_stage: string;
  current_owner: string;
  next_actor: string;
  latest_artifact: string;
  latest_approved_artifact: string;
  approvals: {
    topic_gate?: string;
    outline?: string;
    final?: string;
  };
  process_mode?: string;
  publish?: {
    publish_to_blog?: boolean;
    require_final_human_approval?: boolean;
  };
  blocking_issues?: string[];
  cleanup?: {
    status?: string;
  };
}

interface StageContext {
  workspaceRoot: string;
  workflowConfig: WorkflowConfig;
  articleId: string;
  workItemPath: string;
  workItemDir: string;
  intakePath: string;
  statusPath: string;
  intake: Record<string, any>;
  status: StatusFile;
  dryRun: boolean;
  forceHumanBypass: boolean;
  publish: boolean;
  push: boolean;
}

function fileExists(filePath: string): boolean {
  return fs.existsSync(filePath);
}

function writeYamlFile(filePath: string, data: unknown): void {
  fs.writeFileSync(filePath, yaml.dump(data, { lineWidth: 120, noRefs: true }), 'utf8');
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

function selectQueueItem(workspaceRoot: string, articleId?: string): WorkQueueItem {
  const queue = loadWorkQueue(workspaceRoot);
  if (articleId) {
    const found = queue.items.find((item) => item.article_id === articleId);
    if (!found) {
      throw new Error(`Article ${articleId} was not found in work-queue.yaml.`);
    }

    return found;
  }

  if (queue.items.length === 0) {
    throw new Error('No active work items found in work-queue.yaml.');
  }

  if (queue.items.length > 1) {
    throw new Error('Multiple active work items exist. Pass --article-id to choose one explicitly.');
  }

  return queue.items[0];
}

function createContext(currentDir: string, options?: StageRunnerOptions): StageContext {
  const workspaceRoot = resolveWorkspaceRoot(currentDir, options?.workspaceRoot);
  const workflowConfig = loadWorkflowConfig(workspaceRoot);
  const queueItem = selectQueueItem(workspaceRoot, options?.articleId);
  const workItemDir = path.join(workspaceRoot, queueItem.work_item_path);
  const intakePath = path.join(workItemDir, 'intake.yaml');
  const statusPath = path.join(workItemDir, 'status.yaml');

  if (!fileExists(intakePath) || !fileExists(statusPath)) {
    throw new Error(`Work item ${queueItem.work_item_path} is missing intake.yaml or status.yaml.`);
  }

  return {
    workspaceRoot,
    workflowConfig,
    articleId: queueItem.article_id,
    workItemPath: queueItem.work_item_path,
    workItemDir,
    intakePath,
    statusPath,
    intake: readYamlFile<Record<string, any>>(intakePath),
    status: readYamlFile<StatusFile>(statusPath),
    dryRun: Boolean(options?.dryRun),
    forceHumanBypass: Boolean(options?.forceHumanBypass),
    publish: Boolean(options?.publish),
    push: Boolean(options?.push)
  };
}

function saveStatus(context: StageContext): void {
  if (context.dryRun) {
    return;
  }

  writeYamlFile(context.statusPath, context.status);
}

function refreshStatus(context: StageContext): void {
  context.status = readYamlFile<StatusFile>(context.statusPath);
}

function setStatus(context: StageContext, patch: Partial<StatusFile>): void {
  context.status = {
    ...context.status,
    ...patch,
    approvals: {
      ...(context.status.approvals ?? {}),
      ...(patch.approvals ?? {})
    },
    publish: {
      ...(context.status.publish ?? {}),
      ...(patch.publish ?? {})
    },
    cleanup: {
      ...(context.status.cleanup ?? {}),
      ...(patch.cleanup ?? {})
    }
  };
  saveStatus(context);
}

function readText(filePath: string): string {
  return fs.readFileSync(filePath, 'utf8');
}

function extractTag(text: string, tag: string): string {
  const match = text.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, 'i'));
  if (!match) {
    throw new Error(`Expected <${tag}>...</${tag}> in LLM response.`);
  }

  return match[1].trim();
}

function truncate(value: string, maxLength = 8000): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength)}\n\n[truncated]`;
}

function parseFrontmatterYaml(content: string): Record<string, any> {
  try {
    return (yaml.load(content) as Record<string, any>) ?? {};
  } catch {
    const result: Record<string, any> = {};
    const lines = content.split('\n');
    let currentArrayKey: string | null = null;

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (!line.trim()) {
        continue;
      }

      const arrayMatch = line.match(/^\s*-\s*(.+)$/);
      if (arrayMatch && currentArrayKey) {
        if (!Array.isArray(result[currentArrayKey])) {
          result[currentArrayKey] = [];
        }
        result[currentArrayKey].push(arrayMatch[1].trim().replace(/^['"]|['"]$/g, ''));
        continue;
      }

      const kvMatch = line.match(/^([A-Za-z0-9_]+):\s*(.*)$/);
      if (!kvMatch) {
        continue;
      }

      const [, key, rawValue] = kvMatch;
      const value = rawValue.trim();
      if (value === '') {
        currentArrayKey = key;
        result[key] = [];
        continue;
      }

      currentArrayKey = null;
      result[key] = value.replace(/^['"]|['"]$/g, '');
    }

    return result;
  }
}

function gatherEvidenceBundle(context: StageContext): string {
  const bundle: string[] = [];

  for (const ref of context.intake.evidence_bank_refs ?? []) {
    if (typeof ref !== 'string' || ref.length === 0) {
      continue;
    }

    const [relativePath, anchor] = ref.split('#');
    const absolutePath = path.join(context.workspaceRoot, relativePath);
    if (!fileExists(absolutePath)) {
      bundle.push(`Source missing: ${ref}`);
      continue;
    }

    if (absolutePath.endsWith('.yaml') && anchor) {
      const doc = readYamlFile<Record<string, any>>(absolutePath);
      const evidence = (doc.evidence_bank ?? []).find((item: Record<string, any>) => item.evidence_id === anchor);
      if (evidence) {
        bundle.push(
          [
            `Evidence ${anchor}:`,
            `type: ${evidence.type ?? 'unknown'}`,
            `summary: ${evidence.one_liner ?? ''}`,
            `where: ${evidence.where ?? ''}`,
            `strength: ${evidence.strength ?? ''}`
          ].join('\n')
        );
        continue;
      }
    }

    const content = truncate(readText(absolutePath), 5000);
    bundle.push(`Source ${ref}:\n${content}`);
  }

  for (const item of context.intake.first_party_evidence ?? []) {
    if (item?.note) {
      bundle.push(`First-party note (${item.type ?? 'unknown'}): ${item.note}`);
    }
  }

  return bundle.join('\n\n---\n\n');
}

function buildSystemPrompt(): string {
  return [
    'You are an editorial production agent for Glasgow Research Blog.',
    'Write in English.',
    'Audience: US and EU IT teams, founders, product leaders, B2B SaaS teams, agencies.',
    'Keep a practical, blunt, evidence-led tone.',
    'Do not mention internal repo files, evidence-bank, work-items, source packs, or unpublished artifacts in public-facing copy.',
    'Respect evidence boundaries and avoid fabricated claims.'
  ].join(' ');
}

async function runResearchStage(context: StageContext): Promise<void> {
  const evidenceBundle = gatherEvidenceBundle(context);
  const prompt = [
    'Produce a decision-ready research report in markdown wrapped inside <research_report> tags.',
    'The report should include: Topic summary, Search intent, Audience, Evidence strength, Useful angles, Risks, Recommended thesis directions, and Public-source gaps to verify later.',
    `Topic: ${context.intake.topic}`,
    `Primary keyword: ${context.intake.primary_keyword}`,
    `Secondary keywords: ${(context.intake.secondary_keywords ?? []).join(', ')}`,
    `Audience: ${context.intake.icp_or_audience}`,
    `Desired CTA: ${context.intake.desired_cta}`,
    '',
    'Evidence bundle:',
    evidenceBundle
  ].join('\n');

  const response = await generateText(
    [
      { role: 'system', content: buildSystemPrompt() },
      { role: 'user', content: prompt }
    ],
    { temperature: 0.2 }
  );

  const researchReport = extractTag(response, 'research_report');
  const targetPath = path.join(context.workItemDir, '1-research', 'research-report.md');
  if (!context.dryRun) {
    writeText(targetPath, `${researchReport}\n`);
  }

  setStatus(context, {
    state: 'ready_for_strategy',
    current_stage: 'research',
    current_owner: 'stage-runner',
    next_actor: '2-strategy-agent',
    latest_artifact: '1-research/research-report.md',
    latest_approved_artifact: '1-research/research-report.md',
    blocking_issues: []
  });
}

async function runStrategyStage(context: StageContext): Promise<void> {
  const researchReport = readText(path.join(context.workItemDir, '1-research', 'research-report.md'));
  const prompt = [
    'Produce three markdown artifacts wrapped in tags:',
    '<topic_gate>...</topic_gate>',
    '<serp_intent_brief>...</serp_intent_brief>',
    '<content_brief>...</content_brief>',
    'Topic gate must start with a line `Verdict: pass` or `Verdict: reject`.',
    `Topic: ${context.intake.topic}`,
    `Primary keyword: ${context.intake.primary_keyword}`,
    `Article type: ${context.intake.article_type}`,
    `Process mode: ${context.status.process_mode ?? context.intake.process_mode ?? ''}`,
    '',
    'Research report:',
    researchReport
  ].join('\n');

  const response = await generateText(
    [
      { role: 'system', content: buildSystemPrompt() },
      { role: 'user', content: prompt }
    ],
    { temperature: 0.25 }
  );

  const topicGate = extractTag(response, 'topic_gate');
  const serpIntentBrief = extractTag(response, 'serp_intent_brief');
  const contentBrief = extractTag(response, 'content_brief');
  const verdictMatch = topicGate.match(/Verdict:\s*(pass|reject)/i);
  const verdict = verdictMatch?.[1]?.toLowerCase() ?? 'reject';

  if (!context.dryRun) {
    writeText(path.join(context.workItemDir, '2-strategy', 'topic-gate.md'), `${topicGate}\n`);
    writeText(path.join(context.workItemDir, '2-strategy', 'serp-intent-brief.md'), `${serpIntentBrief}\n`);
    writeText(path.join(context.workItemDir, '2-strategy', 'content-brief.md'), `${contentBrief}\n`);
  }

  if (verdict !== 'pass') {
    setStatus(context, {
      state: 'blocked',
      current_stage: 'strategy',
      current_owner: 'stage-runner',
      next_actor: 'human-reviewer',
      latest_artifact: '2-strategy/topic-gate.md',
      latest_approved_artifact: '1-research/research-report.md',
      approvals: {
        topic_gate: 'reject'
      },
      blocking_issues: ['Topic gate rejected the article. Human review is required.']
    });
    return;
  }

  if ((context.status.process_mode ?? '').toUpperCase() === 'C' && !context.forceHumanBypass) {
    setStatus(context, {
      state: 'awaiting_human_review',
      current_stage: 'strategy',
      current_owner: 'stage-runner',
      next_actor: 'human-reviewer',
      latest_artifact: '2-strategy/content-brief.md',
      latest_approved_artifact: '2-strategy/content-brief.md',
      approvals: {
        topic_gate: 'pass'
      },
      blocking_issues: ['Process C requires a human checkpoint after strategy.']
    });
    return;
  }

  setStatus(context, {
    state: 'ready_for_writing',
    current_stage: 'strategy',
    current_owner: 'stage-runner',
    next_actor: '3-writer-agent',
    latest_artifact: '2-strategy/content-brief.md',
    latest_approved_artifact: '2-strategy/content-brief.md',
    approvals: {
      topic_gate: 'pass'
    },
    blocking_issues: []
  });
}

async function runWritingStage(context: StageContext): Promise<void> {
  const contentBrief = readText(path.join(context.workItemDir, '2-strategy', 'content-brief.md'));
  const serpIntentBrief = readText(path.join(context.workItemDir, '2-strategy', 'serp-intent-brief.md'));
  const prompt = [
    'Produce two markdown artifacts wrapped in tags:',
    '<outline>...</outline>',
    '<draft>...</draft>',
    'The draft must be a full article in English with a strong intro, useful sections, concrete evidence-led claims, and a CTA near the end.',
    'Do not include frontmatter. Do not mention internal repo paths or evidence ids.',
    '',
    `Topic: ${context.intake.topic}`,
    `Primary keyword: ${context.intake.primary_keyword}`,
    `Audience: ${context.intake.icp_or_audience}`,
    `Desired CTA: ${context.intake.desired_cta}`,
    '',
    'Content brief:',
    contentBrief,
    '',
    'SERP and intent brief:',
    serpIntentBrief
  ].join('\n');

  const response = await generateText(
    [
      { role: 'system', content: buildSystemPrompt() },
      { role: 'user', content: prompt }
    ],
    { temperature: 0.35 }
  );

  const outline = extractTag(response, 'outline');
  const draft = extractTag(response, 'draft');

  if (!context.dryRun) {
    writeText(path.join(context.workItemDir, '3-writing', 'outline.md'), `${outline}\n`);
    writeText(path.join(context.workItemDir, '3-writing', 'draft-v1.md'), `${draft}\n`);
  }

  setStatus(context, {
    state: 'ready_for_editing',
    current_stage: 'writing',
    current_owner: 'stage-runner',
    next_actor: '4-editor-agent',
    latest_artifact: '3-writing/draft-v1.md',
    latest_approved_artifact: '3-writing/draft-v1.md',
    approvals: {
      outline: context.forceHumanBypass ? 'approved' : 'generated'
    },
    blocking_issues: []
  });
}

async function runEditingStage(context: StageContext): Promise<void> {
  const contentBrief = readText(path.join(context.workItemDir, '2-strategy', 'content-brief.md'));
  const draft = readText(path.join(context.workItemDir, '3-writing', 'draft-v1.md'));
  const prompt = [
    'Produce two markdown artifacts wrapped in tags:',
    '<editorial_review>...</editorial_review>',
    '<edited_draft>...</edited_draft>',
    'The editorial review must include a readiness verdict.',
    'The edited draft must stay public-safe and remove any internal-only wording.',
    '',
    'Content brief:',
    contentBrief,
    '',
    'Draft:',
    draft
  ].join('\n');

  const response = await generateText(
    [
      { role: 'system', content: buildSystemPrompt() },
      { role: 'user', content: prompt }
    ],
    { temperature: 0.25 }
  );

  const review = extractTag(response, 'editorial_review');
  const editedDraft = extractTag(response, 'edited_draft');

  if (!context.dryRun) {
    writeText(path.join(context.workItemDir, '4-editing', 'editorial-review-v1.md'), `${review}\n`);
    writeText(path.join(context.workItemDir, '4-editing', 'draft-edited-v1.md'), `${editedDraft}\n`);
  }

  setStatus(context, {
    state: 'ready_for_publish',
    current_stage: 'editing',
    current_owner: 'stage-runner',
    next_actor: '5-publisher-agent',
    latest_artifact: '4-editing/draft-edited-v1.md',
    latest_approved_artifact: '4-editing/draft-edited-v1.md',
    blocking_issues: []
  });
}

function validatePublicContent(articleMarkdown: string): void {
  const forbiddenPatterns = [
    /work-items\//i,
    /archive\//i,
    /evidence-bank\//i,
    /source pack/i,
    /\.ya?ml/i
  ];

  for (const pattern of forbiddenPatterns) {
    if (pattern.test(articleMarkdown)) {
      throw new Error(`Public article failed audit due to forbidden pattern: ${pattern}`);
    }
  }
}

async function runPublisherStage(context: StageContext): Promise<{ slug: string; blogFilePath?: string }> {
  const editedDraft = readText(path.join(context.workItemDir, '4-editing', 'draft-edited-v1.md'));
  const prompt = [
    'Produce three artifacts wrapped in tags:',
    '<frontmatter_yaml>...</frontmatter_yaml>',
    '<publish_package>...</publish_package>',
    '<final_article>...</final_article>',
    'Frontmatter YAML must contain slug, title, description, pubDate, author, authorSlug, category, tags.',
    'Wrap every scalar string value in double quotes.',
    `Use pubDate "${new Date().toISOString()}".`,
    'Final article must be public-safe markdown body without frontmatter.',
    'Publish package must be markdown.',
    '',
    `Topic: ${context.intake.topic}`,
    `Primary keyword: ${context.intake.primary_keyword}`,
    `Desired CTA: ${context.intake.desired_cta}`,
    '',
    'Edited draft:',
    editedDraft
  ].join('\n');

  const response = await generateText(
    [
      { role: 'system', content: buildSystemPrompt() },
      { role: 'user', content: prompt }
    ],
    { temperature: 0.2 }
  );

  const frontmatterYaml = extractTag(response, 'frontmatter_yaml');
  const publishPackage = extractTag(response, 'publish_package');
  const finalArticleBody = extractTag(response, 'final_article');
  const frontmatter = parseFrontmatterYaml(frontmatterYaml);
  const slug = String(frontmatter.slug ?? '').trim();
  if (!slug) {
    throw new Error('Publisher stage did not produce a slug.');
  }

  const finalArticle = `---\n${frontmatterYaml.trim()}\n---\n\n${finalArticleBody.trim()}\n`;
  validatePublicContent(finalArticleBody);

  if (!context.dryRun) {
    writeText(path.join(context.workItemDir, '5-publish', 'frontmatter.yaml'), `${frontmatterYaml.trim()}\n`);
    writeText(path.join(context.workItemDir, '5-publish', 'publish-package.md'), `${publishPackage}\n`);
    writeText(path.join(context.workItemDir, '5-publish', 'final-article.md'), finalArticle);
  }

  const requiresFinalApproval = context.status.publish?.require_final_human_approval ?? true;
  const publishToBlog = context.publish || context.status.publish?.publish_to_blog || context.workflowConfig.mode?.publish_to_blog;

  if (requiresFinalApproval && !context.forceHumanBypass && !context.publish) {
    setStatus(context, {
      state: 'awaiting_final_approval',
      current_stage: 'publish',
      current_owner: 'stage-runner',
      next_actor: 'human-reviewer',
      latest_artifact: '5-publish/final-article.md',
      latest_approved_artifact: '5-publish/final-article.md',
      approvals: {
        final: 'pending'
      },
      blocking_issues: ['Final human approval is required before writing to blog.']
    });
    return { slug };
  }

  let blogFilePath: string | undefined;
  if (publishToBlog) {
    blogFilePath = publishArticleToBlog(context, slug, finalArticle);
    setStatus(context, {
      approvals: {
        final: 'approved'
      },
      publish: {
        publish_to_blog: true
      }
    });
  } else {
    setStatus(context, {
      state: 'publish_ready',
      current_stage: 'publish',
      current_owner: 'stage-runner',
      next_actor: 'human-reviewer',
      latest_artifact: '5-publish/final-article.md',
      latest_approved_artifact: '5-publish/final-article.md',
      approvals: {
        final: 'approved'
      },
      blocking_issues: ['Final article package is ready, but publish_to_blog is disabled.']
    });
  }

  return { slug, blogFilePath };
}

function publishArticleToBlog(context: StageContext, slug: string, finalArticle: string): string {
  const blogContentDir = path.join(
    context.workspaceRoot,
    context.workflowConfig.paths?.blog_content_dir ?? 'blog/src/content/blog'
  );
  ensureDir(blogContentDir);
  const blogFilePath = path.join(blogContentDir, `${slug}.md`);

  if (!context.dryRun) {
    writeText(blogFilePath, finalArticle);
    execFileSync('npm', ['run', 'build'], {
      cwd: path.join(context.workspaceRoot, 'blog'),
      stdio: 'pipe'
    });
  }

  setStatus(context, {
    state: 'published',
    current_stage: 'publish',
    current_owner: 'stage-runner',
    next_actor: '0-orchestrator-agent',
    latest_artifact: '5-publish/final-article.md',
    latest_approved_artifact: '5-publish/final-article.md',
    blocking_issues: []
  });

  return blogFilePath;
}

function getCurrentBranch(blogRepoDir: string): string {
  return execFileSync('git', ['branch', '--show-current'], { cwd: blogRepoDir, encoding: 'utf8' }).trim();
}

function getStagedPaths(blogRepoDir: string): string[] {
  const output = execFileSync('git', ['diff', '--cached', '--name-only'], {
    cwd: blogRepoDir,
    encoding: 'utf8'
  }).trim();
  return output ? output.split('\n').filter(Boolean) : [];
}

function pushBlogCommit(context: StageContext, blogFilePath: string): void {
  const blogRepoDir = path.join(context.workspaceRoot, 'blog');
  const relativeArticlePath = path.relative(blogRepoDir, blogFilePath);
  const stagedPaths = getStagedPaths(blogRepoDir);
  const unrelatedStaged = stagedPaths.filter((entry) => entry !== relativeArticlePath);
  if (unrelatedStaged.length > 0) {
    throw new Error(`Blog repo has staged unrelated changes: ${unrelatedStaged.join(', ')}`);
  }

  if (!context.dryRun) {
    execFileSync('git', ['add', '--', relativeArticlePath], { cwd: blogRepoDir, stdio: 'pipe' });
    execFileSync('git', ['commit', '-m', `Publish article: ${path.basename(relativeArticlePath, '.md')}`, '--', relativeArticlePath], {
      cwd: blogRepoDir,
      stdio: 'pipe'
    });
    execFileSync('git', ['push', 'origin', getCurrentBranch(blogRepoDir)], {
      cwd: blogRepoDir,
      stdio: 'pipe'
    });
  }
}

function archivePublishedWorkItem(context: StageContext, blogFilePath?: string): void {
  const archiveDir = path.join(
    context.workspaceRoot,
    context.workflowConfig.paths?.archive_published_dir ?? 'archive/published',
    context.articleId
  );
  ensureDir(archiveDir);

  const filesToCopy = [
    ['5-publish/final-article.md', 'final-article.md'],
    ['5-publish/frontmatter.yaml', 'frontmatter.yaml'],
    ['5-publish/publish-package.md', 'publish-package.md']
  ] as const;

  if (!context.dryRun) {
    for (const [from, to] of filesToCopy) {
      fs.copyFileSync(path.join(context.workItemDir, from), path.join(archiveDir, to));
    }

    const archiveSummary = {
      article_id: context.articleId,
      archive_kind: 'published',
      archived_at: new Date().toISOString(),
      stage_reached: 'publish',
      reason: blogFilePath ? 'Published to blog and cleaned up from active work-items.' : 'Published package archived.',
      final_destination: blogFilePath ? path.relative(context.workspaceRoot, blogFilePath) : '',
      kept_files: [
        path.relative(context.workspaceRoot, path.join(archiveDir, 'archive-summary.yaml')),
        path.relative(context.workspaceRoot, path.join(archiveDir, 'final-article.md')),
        path.relative(context.workspaceRoot, path.join(archiveDir, 'frontmatter.yaml')),
        path.relative(context.workspaceRoot, path.join(archiveDir, 'publish-package.md'))
      ]
    };
    writeYamlFile(path.join(archiveDir, 'archive-summary.yaml'), archiveSummary);
  }

  const queue = loadWorkQueue(context.workspaceRoot);
  queue.items = queue.items.filter((item) => item.article_id !== context.articleId);
  if (!context.dryRun) {
    saveWorkQueue(context.workspaceRoot, queue);
    fs.rmSync(context.workItemDir, { recursive: true, force: true });
  }
}

function writeStageRunnerReport(
  context: StageContext,
  result: Pick<StageRunnerResult, 'status' | 'articleId' | 'finalStage' | 'reason'>
): string {
  const reportPath = path.join(context.workspaceRoot, 'reports/status/stage-runner-latest.md');
  const lines = [
    '# Stage Runner Report',
    '',
    `Generated at: ${new Date().toISOString()}`,
    `Article ID: ${result.articleId}`,
    `Status: ${result.status}`,
    `Final stage: ${result.finalStage}`,
    `Reason: ${result.reason}`,
    `Dry run: ${context.dryRun ? 'true' : 'false'}`,
    `Publish requested: ${context.publish ? 'true' : 'false'}`,
    `Push requested: ${context.push ? 'true' : 'false'}`
  ];

  writeText(reportPath, `${lines.join('\n')}\n`);
  return reportPath;
}

function stageFileExists(context: StageContext, relativePath: string): boolean {
  return fileExists(path.join(context.workItemDir, relativePath));
}

export async function runStageRunner(currentDir: string, options?: StageRunnerOptions): Promise<StageRunnerResult> {
  const context = createContext(currentDir, options);
  const runLog = createRunLogger('keyword:agent:stage', context.workspaceRoot);

  try {
    runLog.addStep('Start stage runner', `Processing work item ${context.articleId} from stage ${context.status.current_stage}.`);

    if (!stageFileExists(context, '1-research/research-report.md')) {
      runLog.addStep('Research stage', 'Generating research-report.md');
      await runResearchStage(context);
      runLog.addResult('Created 1-research/research-report.md');
      refreshStatus(context);
    }

    if (!stageFileExists(context, '2-strategy/content-brief.md') && context.status.next_actor !== 'human-reviewer') {
      runLog.addStep('Strategy stage', 'Generating topic gate, SERP brief, and content brief.');
      await runStrategyStage(context);
      runLog.addResult('Created strategy artifacts.');
      refreshStatus(context);
    }

    if (context.status.next_actor === 'human-reviewer') {
      const provisional = {
        status: 'stopped' as const,
        articleId: context.articleId,
        finalStage: context.status.current_stage,
        reason: (context.status.blocking_issues ?? []).join(' ') || 'Human review is required before continuing.',
        logPath: ''
      };
      const reportPath = writeStageRunnerReport(context, provisional);
      const logPath = runLog.complete(provisional.reason);
      return { ...provisional, reportPath, logPath, workItemPath: context.workItemPath };
    }

    if (!stageFileExists(context, '3-writing/draft-v1.md')) {
      runLog.addStep('Writing stage', 'Generating outline and first draft.');
      await runWritingStage(context);
      runLog.addResult('Created outline.md and draft-v1.md');
      refreshStatus(context);
    }

    if (!stageFileExists(context, '4-editing/draft-edited-v1.md')) {
      runLog.addStep('Editing stage', 'Generating editorial review and edited draft.');
      await runEditingStage(context);
      runLog.addResult('Created editorial-review-v1.md and draft-edited-v1.md');
      refreshStatus(context);
    }

    let blogFilePath: string | undefined;
    if (!stageFileExists(context, '5-publish/final-article.md')) {
      runLog.addStep('Publisher stage', 'Generating frontmatter, publish package, and final article.');
      const publishResult = await runPublisherStage(context);
      blogFilePath = publishResult.blogFilePath;
      runLog.addResult('Created publish artifacts.');
      if (blogFilePath) {
        runLog.addResult(`Published article file: ${path.relative(context.workspaceRoot, blogFilePath)}`);
      }
      refreshStatus(context);
    } else if (context.publish) {
      const frontmatter = readYamlFile<Record<string, any>>(path.join(context.workItemDir, '5-publish', 'frontmatter.yaml'));
      const slug = String(frontmatter.slug ?? '').trim();
      if (slug) {
        blogFilePath = publishArticleToBlog(
          context,
          slug,
          readText(path.join(context.workItemDir, '5-publish', 'final-article.md'))
        );
      }
    }

    if (context.status.next_actor === 'human-reviewer') {
      const provisional = {
        status: 'stopped' as const,
        articleId: context.articleId,
        finalStage: context.status.current_stage,
        reason: (context.status.blocking_issues ?? []).join(' ') || 'Human review is required before publish.',
        logPath: ''
      };
      const reportPath = writeStageRunnerReport(context, provisional);
      const logPath = runLog.complete(provisional.reason);
      return { ...provisional, reportPath, logPath, workItemPath: context.workItemPath };
    }

    if (blogFilePath && context.push) {
      runLog.addStep('Push stage', 'Committing and pushing the published article in the blog repository.');
      pushBlogCommit(context, blogFilePath);
      runLog.addResult(`Pushed article via blog git repo: ${path.relative(context.workspaceRoot, blogFilePath)}`);
    }

    if (blogFilePath) {
      runLog.addStep('Archive stage', 'Archiving publish package and cleaning up active work item.');
      archivePublishedWorkItem(context, blogFilePath);
      runLog.addResult(`Archived and cleaned up ${context.articleId}`);
    }

    const completed = {
      status: 'completed' as const,
      articleId: context.articleId,
      finalStage: blogFilePath ? 'archive' : context.status.current_stage,
      reason: blogFilePath
        ? 'Article advanced through publish and cleanup.'
        : 'Article advanced through stage generation but was not written to the blog.',
      logPath: ''
    };
    const reportPath = writeStageRunnerReport(context, completed);
    const logPath = runLog.complete(completed.reason);
    return { ...completed, reportPath, logPath, workItemPath: context.workItemPath };
  } catch (error) {
    const logPath = runLog.fail(error, 'Stage runner failed before completing the article lifecycle.');
    throw new Error(`Stage runner failed. Log: ${logPath}. ${error instanceof Error ? error.message : String(error)}`);
  }
}
