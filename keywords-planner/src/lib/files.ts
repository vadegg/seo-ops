import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';
import { parse } from 'csv-parse/sync';
import { stringify } from 'csv-stringify/sync';
import type { RunPaths } from '../types/index.js';

export function ensureDir(dirPath: string): void {
  fs.mkdirSync(dirPath, { recursive: true });
}

export function fileExists(filePath: string): boolean {
  return fs.existsSync(filePath);
}

export function readYamlFile<T>(filePath: string): T {
  return yaml.load(fs.readFileSync(filePath, 'utf8')) as T;
}

export function writeJson(filePath: string, data: unknown): void {
  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

export function readJsonFile<T>(filePath: string): T {
  return JSON.parse(fs.readFileSync(filePath, 'utf8')) as T;
}

export function writeText(filePath: string, text: string): void {
  fs.writeFileSync(filePath, text, 'utf8');
}

export function writeCsv<T extends object>(filePath: string, rows: T[]): void {
  const csv = stringify(rows as Array<Record<string, unknown>>, { header: true });
  fs.writeFileSync(filePath, csv, 'utf8');
}

export function readCsv<T>(filePath: string): T[] {
  const content = fs.readFileSync(filePath, 'utf8');
  return parse(content, { columns: true, skip_empty_lines: true, trim: true }) as T[];
}

export function createRunPaths(rootDir: string, runId: string): RunPaths {
  const runDir = path.join(rootDir, 'runs', runId);
  ensureDir(runDir);

  return {
    runDir,
    expandedCsvPath: path.join(runDir, 'expanded_keywords.csv'),
    reviewCsvPath: path.join(runDir, 'keyword_review.csv'),
    volumeJsonPath: path.join(runDir, 'volume_results.json'),
    serpJsonPath: path.join(runDir, 'serp_analysis.json'),
    analysisJsonPath: path.join(runDir, 'analysis.json'),
    reportPath: path.join(runDir, 'recommendations.md')
  };
}

export function getLatestRunId(rootDir: string): string | null {
  const runsDir = path.join(rootDir, 'runs');
  if (!fileExists(runsDir)) {
    return null;
  }

  const entries = fs
    .readdirSync(runsDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();

  return entries.at(-1) ?? null;
}
