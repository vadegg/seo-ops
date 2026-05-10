import { normalizeKeyword } from '../../lib/keywords.js';
import type { ReviewRow, SeedKeyword } from '../../types/index.js';

export function buildReviewRows(keywords: SeedKeyword[]): ReviewRow[] {
  return keywords.map((entry) => ({
    keyword: entry.keyword,
    normalized_keyword: normalizeKeyword(entry.keyword),
    source: entry.source,
    seed_origin: entry.seed_origin ?? entry.keyword,
    include: entry.source === 'manual' ? 'true' : 'false',
    priority_hint: entry.source === 'manual' ? 'high' : '',
    notes: ''
  }));
}

export function parseReviewRows(rows: ReviewRow[]): ReviewRow[] {
  return rows
    .filter((row) => row.keyword.trim().length > 0)
    .map((row) => ({
      ...row,
      keyword: normalizeKeyword(row.keyword),
      normalized_keyword: normalizeKeyword(row.keyword),
      source: row.source === 'manual' ? 'manual' : 'generated',
      seed_origin: row.seed_origin ? normalizeKeyword(row.seed_origin) : normalizeKeyword(row.keyword),
      include: row.include.trim().toLowerCase(),
      priority_hint: row.priority_hint?.trim() ?? '',
      notes: row.notes?.trim() ?? ''
    }));
}

export function isIncluded(value: string): boolean {
  return ['true', '1', 'yes', 'y'].includes(value.trim().toLowerCase());
}
