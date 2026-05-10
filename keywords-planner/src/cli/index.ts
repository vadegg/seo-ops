import path from 'node:path';
import { parseArgs } from '../lib/cli.js';
import { loadEnvironment } from '../lib/env.js';
import {
  createRunPaths,
  getLatestRunId,
  readCsv,
  readJsonFile,
  readYamlFile,
  writeCsv,
  writeJson,
  writeText
} from '../lib/files.js';
import { buildReviewRows, parseReviewRows } from '../modules/human-review/index.js';
import { buildKeywordAnalyses, buildRecommendationsReport } from '../modules/recommendation-engine/index.js';
import { expandSeeds } from '../modules/seed-expander/index.js';
import { analyzeSerp } from '../modules/serp-analyzer/index.js';
import { collectVolumeForReviewRows } from '../modules/volume-collector/index.js';
import type { KeywordAnalysis, ModifiersConfig, ReviewRow, SeedsFile, SiteProfile } from '../types/index.js';

function getRootDir(): string {
  return process.cwd();
}

function getRunId(): string {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

async function runStart(rootDir: string): Promise<void> {
  const siteProfile = readYamlFile<SiteProfile>(path.join(rootDir, 'config', 'site-profile.yaml'));
  const modifiers = readYamlFile<ModifiersConfig>(path.join(rootDir, 'config', 'modifiers.yaml'));
  const seeds = readYamlFile<SeedsFile>(path.join(rootDir, 'input', 'seeds.yaml'));

  const runId = getRunId();
  const runPaths = createRunPaths(rootDir, runId);
  const expanded = expandSeeds(seeds.seeds, modifiers);
  const reviewRows = buildReviewRows(expanded);

  writeCsv(runPaths.expandedCsvPath, expanded);
  writeCsv(runPaths.reviewCsvPath, reviewRows);

  console.log(`Created run: ${runId}`);
  console.log(`Site: ${siteProfile.site_name}`);
  console.log(`Expanded ${expanded.length} candidate keywords.`);
  console.log(`Review file: ${runPaths.reviewCsvPath}`);
  console.log('Next step: open the review CSV, mark include=true for the keywords you want, add manual rows if needed, then run `npm run keyword:agent:resume -- --run-id <id>`.');
}

async function runResume(rootDir: string, runIdArg?: string, skipVolume = false): Promise<void> {
  const runId = runIdArg ?? getLatestRunId(rootDir);
  if (!runId) {
    throw new Error('No run found. Start a run first with `npm run keyword:agent:start`.');
  }

  const siteProfile = readYamlFile<SiteProfile>(path.join(rootDir, 'config', 'site-profile.yaml'));
  const runPaths = createRunPaths(rootDir, runId);
  const reviewRows = parseReviewRows(readCsv<ReviewRow>(runPaths.reviewCsvPath));

  const volumes = skipVolume ? [] : await collectVolumeForReviewRows(reviewRows, rootDir);
  const includedKeywords = reviewRows.filter((row) => ['true', '1', 'yes', 'y'].includes(row.include)).map((row) => row.keyword);
  const uniqueKeywords = [...new Set(includedKeywords)];
  const serps = [];

  for (const keyword of uniqueKeywords) {
    serps.push(await analyzeSerp(keyword));
  }

  const analyses = buildKeywordAnalyses(reviewRows, volumes, serps, siteProfile);
  const report = buildRecommendationsReport(analyses);

  writeJson(runPaths.volumeJsonPath, volumes);
  writeJson(runPaths.serpJsonPath, serps);
  writeJson(runPaths.analysisJsonPath, analyses);
  writeText(runPaths.reportPath, report);

  console.log(`Completed run: ${runId}`);
  console.log(`Analyzed keywords: ${uniqueKeywords.length}`);
  console.log(`Report: ${runPaths.reportPath}`);
}

async function runVolume(rootDir: string, runIdArg?: string): Promise<void> {
  const runId = runIdArg ?? getLatestRunId(rootDir);
  if (!runId) {
    throw new Error('No run found. Start a run first with `npm run keyword:agent:start`.');
  }

  const runPaths = createRunPaths(rootDir, runId);
  const reviewRows = parseReviewRows(readCsv<ReviewRow>(runPaths.reviewCsvPath));
  const volumes = await collectVolumeForReviewRows(reviewRows, rootDir);
  writeJson(runPaths.volumeJsonPath, volumes);
  console.log(`Volume results saved to ${runPaths.volumeJsonPath}`);
}

async function runReport(rootDir: string, runIdArg?: string): Promise<void> {
  const runId = runIdArg ?? getLatestRunId(rootDir);
  if (!runId) {
    throw new Error('No run found.');
  }

  const runPaths = createRunPaths(rootDir, runId);
  const analyses = readJsonFile<KeywordAnalysis[]>(runPaths.analysisJsonPath);
  const report = buildRecommendationsReport(analyses);
  writeText(runPaths.reportPath, report);
  console.log(`Rebuilt report for run ${runId}`);
  console.log(`Report: ${runPaths.reportPath}`);
}

async function main(): Promise<void> {
  const rootDir = getRootDir();
  loadEnvironment(rootDir);
  const { command, flags } = parseArgs(process.argv.slice(2));

  switch (command) {
    case 'start':
      await runStart(rootDir);
      return;
    case 'resume':
      await runResume(rootDir, typeof flags['run-id'] === 'string' ? flags['run-id'] : undefined, Boolean(flags['skip-volume']));
      return;
    case 'volume':
      await runVolume(rootDir, typeof flags['run-id'] === 'string' ? flags['run-id'] : undefined);
      return;
    case 'report':
      await runReport(rootDir, typeof flags['run-id'] === 'string' ? flags['run-id'] : undefined);
      return;
    default:
      console.log(
        'Usage: tsx src/cli/index.ts <start|resume|volume|report> [--run-id <id>] [--skip-volume]'
      );
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
