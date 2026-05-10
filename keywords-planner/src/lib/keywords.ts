import type { ModifiersConfig, SeedKeyword, SiteProfile } from '../types/index.js';

export function normalizeKeyword(keyword: string): string {
  return keyword.trim().toLowerCase().replace(/\s+/g, ' ');
}

export function uniqueKeywords(keywords: SeedKeyword[]): SeedKeyword[] {
  const seen = new Set<string>();
  const unique: SeedKeyword[] = [];

  for (const keyword of keywords) {
    const normalized = normalizeKeyword(keyword.keyword);
    if (seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    unique.push({ ...keyword, keyword: normalized });
  }

  return unique;
}

export function expandSeedKeywords(seeds: string[], modifiers: ModifiersConfig): SeedKeyword[] {
  const candidates: SeedKeyword[] = seeds.map((seed) => ({
    keyword: normalizeKeyword(seed),
    source: 'manual',
    seed_origin: normalizeKeyword(seed)
  }));

  for (const rawSeed of seeds) {
    const seed = normalizeKeyword(rawSeed);

    for (const prefix of modifiers.generic_prefixes) {
      candidates.push({
        keyword: `${prefix} ${seed}`,
        source: 'generated',
        seed_origin: seed
      });
    }

    for (const suffix of modifiers.generic_suffixes) {
      candidates.push({
        keyword: `${seed} ${suffix}`,
        source: 'generated',
        seed_origin: seed
      });
    }

    for (const suffix of modifiers.audience_suffixes) {
      candidates.push({
        keyword: `${seed} ${suffix}`,
        source: 'generated',
        seed_origin: seed
      });
    }

    for (const suffix of modifiers.intent_suffixes) {
      candidates.push({
        keyword: `${seed} ${suffix}`,
        source: 'generated',
        seed_origin: seed
      });
    }
  }

  const comparisons = seeds.flatMap((left, index) =>
    seeds.slice(index + 1).map((right) => ({
      keyword: `${normalizeKeyword(left)} vs ${normalizeKeyword(right)}`,
      source: 'generated' as const,
      seed_origin: normalizeKeyword(left)
    }))
  );

  return uniqueKeywords([...candidates, ...comparisons]);
}

export function scoreSiteFit(keyword: string, siteProfile: SiteProfile): number {
  const normalized = normalizeKeyword(keyword);

  if (
    siteProfile.excluded_topics.some((topic) => normalized.includes(normalizeKeyword(topic).split(' ')[0] ?? ''))
  ) {
    return 0;
  }

  const topicMatches = siteProfile.core_topics.reduce((score, topic) => {
    const topicTokens = normalizeKeyword(topic).split(' ');
    const matches = topicTokens.filter((token) => token.length > 2 && normalized.includes(token)).length;
    return score + matches;
  }, 0);

  if (topicMatches >= 4) {
    return 5;
  }
  if (topicMatches >= 2) {
    return 4;
  }
  if (topicMatches >= 1) {
    return 3;
  }

  return 1;
}

export function scoreServiceAdjacency(keyword: string, siteProfile: SiteProfile): number {
  const normalized = normalizeKeyword(keyword);
  const serviceKeywords = siteProfile.service_keywords ?? [];
  const matches = serviceKeywords.filter((token) => normalized.includes(normalizeKeyword(token))).length;

  if (matches >= 3) {
    return 5;
  }
  if (matches >= 2) {
    return 4;
  }
  if (matches >= 1) {
    return 3;
  }
  return 1;
}

export function guessContentType(
  keyword: string,
  intent: 'informational' | 'how-to' | 'commercial-investigation' | 'comparison' | 'mixed',
  demandScore: number
):
  | 'glossary'
  | 'how-to'
  | 'comparison'
  | 'checklist/template'
  | 'opinionated expert article'
  | 'pillar article' {
  const normalized = normalizeKeyword(keyword);

  if (normalized.includes('template') || normalized.includes('checklist')) {
    return 'checklist/template';
  }
  if (normalized.includes('vs')) {
    return 'comparison';
  }
  if (normalized.startsWith('how to') || intent === 'how-to') {
    return 'how-to';
  }
  if (normalized.includes('what is') || normalized.split(' ').length <= 2) {
    return demandScore >= 4 ? 'pillar article' : 'glossary';
  }

  return demandScore >= 4 ? 'pillar article' : 'opinionated expert article';
}

export function scoreDemand(volumeAvgMonthly: number | null): number {
  if (volumeAvgMonthly === null) {
    return 0;
  }
  if (volumeAvgMonthly >= 5000) {
    return 5;
  }
  if (volumeAvgMonthly >= 1000) {
    return 4;
  }
  if (volumeAvgMonthly >= 200) {
    return 3;
  }
  if (volumeAvgMonthly >= 50) {
    return 2;
  }
  return 1;
}
