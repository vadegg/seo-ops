import fs from 'node:fs';
import path from 'node:path';
import dotenv from 'dotenv';

function loadIfExists(filePath: string): void {
  if (!fs.existsSync(filePath)) {
    return;
  }

  dotenv.config({ path: filePath, override: false, quiet: true });
}

export function loadEnvironment(currentDir: string): void {
  loadIfExists(path.join(currentDir, '.env'));
  loadIfExists(path.join(currentDir, '.env.local'));
}
