import fs from 'node:fs';
import path from 'node:path';
import { readYamlFile } from './files.js';

export interface RunnerConfig {
  backlog_files?: string[];
  evidence_index_file?: string;
  editorial?: {
    brand_name?: string;
    language?: string;
    geo_targets?: string[];
    audience?: string;
    tone?: string;
    author_name?: string;
    author_slug?: string;
    default_category?: string;
  };
  publishing?: {
    blog_repo_root?: string;
    blog_content_dir?: string;
  };
}

export function resolveWorkspacePath(workspaceRoot: string, configuredPath: string | undefined, fallback: string): string {
  const finalPath = configuredPath && configuredPath.length > 0 ? configuredPath : fallback;
  return path.isAbsolute(finalPath) ? finalPath : path.join(workspaceRoot, finalPath);
}

function listFilesRecursively(dirPath: string): string[] {
  if (!fs.existsSync(dirPath)) {
    return [];
  }

  const output: string[] = [];
  for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      output.push(...listFilesRecursively(fullPath));
    } else {
      output.push(fullPath);
    }
  }

  return output;
}

export function loadRunnerConfig(workspaceRoot: string): RunnerConfig {
  const configPath = path.join(workspaceRoot, 'runner-config.yaml');
  if (!fs.existsSync(configPath)) {
    return {};
  }

  return readYamlFile<RunnerConfig>(configPath);
}

export function getBacklogFiles(workspaceRoot: string, runnerConfig: RunnerConfig): string[] {
  if (runnerConfig.backlog_files && runnerConfig.backlog_files.length > 0) {
    return runnerConfig.backlog_files.map((filePath) => resolveWorkspacePath(workspaceRoot, filePath, filePath));
  }

  const strategyDir = path.join(workspaceRoot, 'strategy');
  return listFilesRecursively(strategyDir).filter((filePath) => /backlog.*\.ya?ml$/i.test(path.basename(filePath)));
}
