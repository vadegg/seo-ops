import { fetchSearchResults } from '../../providers/search/index.js';
import type { SearchResultEntry, SerpAnalysis } from '../../types/index.js';

function classifyResultType(entry: SearchResultEntry): string {
  const domain = entry.domain;
  const text = `${entry.title} ${entry.snippet}`.toLowerCase();

  if (domain.includes('reddit.com') || domain.includes('quora.com')) {
    return 'forum';
  }
  if (domain.includes('youtube.com')) {
    return 'video';
  }
  if (domain.includes('wikipedia.org')) {
    return 'wiki';
  }
  if (domain.includes('coursera.org') || domain.includes('udemy.com')) {
    return 'course';
  }
  if (text.includes('template') || text.includes('checklist')) {
    return 'template/resource';
  }
  if (text.includes('best') || text.includes('software') || text.includes('tool')) {
    return 'vendor roundup';
  }
  if (domain.includes('docs.') || text.includes('documentation')) {
    return 'docs';
  }
  if (text.includes('agency') || text.includes('services')) {
    return 'service page';
  }

  return 'article';
}

function inferIntent(keyword: string, resultTypes: string[]): SerpAnalysis['dominant_intent'] {
  const normalized = keyword.toLowerCase();

  if (normalized.includes(' vs ')) {
    return 'comparison';
  }
  if (normalized.startsWith('how to') || normalized.includes('guide') || normalized.includes('process')) {
    return 'how-to';
  }
  if (normalized.includes('best') || normalized.includes('tool') || normalized.includes('software')) {
    return 'commercial-investigation';
  }

  const uniqueTypes = new Set(resultTypes);
  if (uniqueTypes.has('service page') && uniqueTypes.has('article')) {
    return 'mixed';
  }

  return 'informational';
}

function scoreOpportunity(resultTypes: string[], domains: string[]): number {
  let score = 3;
  const uniqueTypes = new Set(resultTypes);
  const strongDomains = domains.filter((domain) =>
    ['wikipedia.org', 'coursera.org', 'hubspot.com', 'indeed.com', 'shopify.com', 'gartner.com'].some((strong) =>
      domain.includes(strong)
    )
  ).length;

  if (uniqueTypes.has('forum') || uniqueTypes.has('template/resource')) {
    score += 1;
  }
  if (uniqueTypes.has('service page') && uniqueTypes.has('article')) {
    score += 1;
  }
  if (strongDomains >= 4) {
    score -= 2;
  }
  if (strongDomains >= 2) {
    score -= 1;
  }

  return Math.max(1, Math.min(5, score));
}

export async function analyzeSerp(keyword: string): Promise<SerpAnalysis> {
  const results = await fetchSearchResults(keyword);
  const topDomains = results.map((entry) => entry.domain).filter(Boolean).slice(0, 5);
  const resultTypes = results.map(classifyResultType);
  const dominantIntent = inferIntent(keyword, resultTypes);
  const opportunityScore = scoreOpportunity(resultTypes, topDomains);
  const serpFeatures: string[] = [];

  if (resultTypes.includes('forum')) {
    serpFeatures.push('community results');
  }
  if (resultTypes.includes('video')) {
    serpFeatures.push('video-friendly SERP');
  }
  if (resultTypes.includes('template/resource')) {
    serpFeatures.push('resource intent');
  }

  const opportunitySummary =
    opportunityScore >= 4
      ? 'SERP looks attackable: mixed result types or visible format gaps are present.'
      : opportunityScore >= 3
        ? 'SERP is competitive but still workable with a sharper angle and stronger evidence.'
        : 'SERP is crowded with strong domains or converged result formats.';

  return {
    keyword,
    dominant_intent: dominantIntent,
    top_result_types: [...new Set(resultTypes)].slice(0, 5),
    top_domains: topDomains,
    serp_features: serpFeatures,
    opportunity_summary: opportunitySummary,
    opportunity_score: opportunityScore
  };
}
