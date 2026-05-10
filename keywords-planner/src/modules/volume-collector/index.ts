import path from 'node:path';
import { collectKeywordPlannerVolumes } from '../../providers/keyword-planner-browser/index.js';
import type { ReviewRow, VolumeResult } from '../../types/index.js';
import { isIncluded } from '../human-review/index.js';

export async function collectVolumeForReviewRows(
  rows: ReviewRow[],
  rootDir: string
): Promise<VolumeResult[]> {
  const selected = rows.filter((row) => isIncluded(row.include)).map((row) => row.keyword);
  const unique = [...new Set(selected)];

  if (unique.length === 0) {
    return [];
  }

  const batches: string[][] = [];
  const batchSize = 10;

  for (let index = 0; index < unique.length; index += batchSize) {
    batches.push(unique.slice(index, index + batchSize));
  }

  const results: VolumeResult[] = [];

  for (const batch of batches) {
    const batchResults = await collectKeywordPlannerVolumes(batch, path.join(rootDir, 'artifacts'));
    results.push(...batchResults);
  }

  return results;
}
