import fs from 'node:fs';
import path from 'node:path';
import dotenv from 'dotenv';

function loadIfExists(filePath: string): void {
  if (!fs.existsSync(filePath)) {
    return;
  }

  dotenv.config({ path: filePath, override: false });
}

export function loadEnvironment(currentDir: string): void {
  const localEnv = path.join(currentDir, '.env');
  const workspaceEnv = path.resolve(currentDir, '..', '.env');

  loadIfExists(workspaceEnv);
  loadIfExists(localEnv);
}
