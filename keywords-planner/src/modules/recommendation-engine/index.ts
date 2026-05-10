import { guessContentType, scoreDemand, scoreServiceAdjacency, scoreSiteFit } from '../../lib/keywords.js';
import type { KeywordAnalysis, ReviewRow, SerpAnalysis, SiteProfile, VolumeResult } from '../../types/index.js';
import { isIncluded } from '../human-review/index.js';

function recommendationReasoning(
  keyword: string,
  demandScore: number,
  siteFitScore: number,
  opportunityScore: number,
  recommendation: KeywordAnalysis['recommendation']
): string {
  if (recommendation === 'target now') {
    return `${keyword} already shows usable demand, strong fit with the site, and a SERP that looks beatable with sharper expertise-led content.`;
  }
  if (recommendation === 'cluster later') {
    return `${keyword} fits the site, but it looks better as supporting coverage or as part of a broader topic cluster than as an immediate standalone bet.`;
  }
  if (recommendation === 'manual review') {
    return `${keyword} may be promising, but the current evidence is incomplete: demand or SERP clarity is still not strong enough for an automatic push.`;
  }

  return `${keyword} is currently a weak target because fit, opportunity, or demand is too low relative to stronger alternatives.`;
}

export function buildKeywordAnalyses(
  rows: ReviewRow[],
  volumes: VolumeResult[],
  serps: SerpAnalysis[],
  siteProfile: SiteProfile
): KeywordAnalysis[] {
  return rows
    .filter((row) => isIncluded(row.include))
    .map((row) => {
      const volume = volumes.find((entry) => entry.keyword === row.keyword);
      const serp = serps.find((entry) => entry.keyword === row.keyword);
      const volumeAvgMonthly = volume?.volume_avg_monthly ?? null;
      const demandScore = scoreDemand(volumeAvgMonthly);
      const siteFitScore = scoreSiteFit(row.keyword, siteProfile);
      const serviceAdjacencyScore = scoreServiceAdjacency(row.keyword, siteProfile);
      const opportunityScore = serp?.opportunity_score ?? 2;

      let recommendation: KeywordAnalysis['recommendation'] = 'cluster later';
      if (siteFitScore <= 1) {
        recommendation = 'skip';
      } else if (volumeAvgMonthly === null && siteFitScore >= 4) {
        recommendation = 'manual review';
      } else if (demandScore >= 2 && siteFitScore >= 3 && opportunityScore >= 3) {
        recommendation = 'target now';
      } else if (siteFitScore >= 3) {
        recommendation = 'cluster later';
      } else {
        recommendation = 'skip';
      }

      const confidence: KeywordAnalysis['confidence'] =
        demandScore >= 2 && siteFitScore >= 3 && opportunityScore >= 3
          ? 'high'
          : demandScore >= 1 && siteFitScore >= 3
            ? 'medium'
            : 'low';

      return {
        keyword: row.keyword,
        seed_origin: row.seed_origin,
        source: row.source,
        include: true,
        priority_hint: row.priority_hint || undefined,
        notes: row.notes || undefined,
        volume_avg_monthly: volumeAvgMonthly,
        volume_range_raw: volume?.volume_range_raw ?? null,
        three_month_change: volume?.three_month_change ?? null,
        yoy_change: volume?.yoy_change ?? null,
        volume_source: volume?.source ?? 'keyword_planner_browser',
        intent: serp?.dominant_intent ?? 'mixed',
        serp_features: serp?.serp_features ?? [],
        top_result_types: serp?.top_result_types ?? [],
        top_domains: serp?.top_domains ?? [],
        site_fit_score: siteFitScore,
        opportunity_score: opportunityScore,
        service_adjacency_score: serviceAdjacencyScore,
        demand_score: demandScore,
        recommendation,
        recommended_content_type: guessContentType(row.keyword, serp?.dominant_intent ?? 'mixed', demandScore),
        reasoning: recommendationReasoning(row.keyword, demandScore, siteFitScore, opportunityScore, recommendation),
        confidence
      };
    })
    .sort((left, right) => {
      const recommendationRank = {
        'target now': 0,
        'cluster later': 1,
        'manual review': 2,
        skip: 3
      };
      return (
        recommendationRank[left.recommendation] - recommendationRank[right.recommendation] ||
        right.site_fit_score + right.opportunity_score + right.demand_score -
          (left.site_fit_score + left.opportunity_score + left.demand_score)
      );
    });
}

export function buildRecommendationsReport(analyses: KeywordAnalysis[]): string {
  const sections: Array<{ title: string; items: KeywordAnalysis[] }> = [
    { title: 'Hit now', items: analyses.filter((item) => item.recommendation === 'target now') },
    { title: 'Cluster later', items: analyses.filter((item) => item.recommendation === 'cluster later') },
    { title: 'Needs manual review', items: analyses.filter((item) => item.recommendation === 'manual review') },
    { title: 'Skip', items: analyses.filter((item) => item.recommendation === 'skip') }
  ];

  const lines = ['# Keyword Recommendations', ''];

  for (const section of sections) {
    lines.push(`## ${section.title}`, '');

    if (section.items.length === 0) {
      lines.push('- Nothing in this bucket yet.', '');
      continue;
    }

    for (const item of section.items) {
      lines.push(`- \`${item.keyword}\``);
      lines.push(
        `  Volume: ${item.volume_range_raw ?? 'missing'} | Intent: ${item.intent} | Type: ${item.recommended_content_type}`
      );
      lines.push(`  Why: ${item.reasoning}`);
    }

    lines.push('');
  }

  return `${lines.join('\n').trim()}\n`;
}
