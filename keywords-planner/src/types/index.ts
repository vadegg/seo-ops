export interface SiteProfile {
  site_name: string;
  site_url: string;
  blog_url: string;
  language: string;
  target_markets: string[];
  audiences: string[];
  core_topics: string[];
  excluded_topics: string[];
  service_keywords?: string[];
}

export interface ModifiersConfig {
  generic_prefixes: string[];
  generic_suffixes: string[];
  audience_suffixes: string[];
  intent_suffixes: string[];
}

export interface SeedsFile {
  seeds: string[];
}

export interface SeedKeyword {
  keyword: string;
  source: 'manual' | 'generated';
  seed_origin?: string;
}

export interface ReviewRow {
  keyword: string;
  normalized_keyword: string;
  source: 'manual' | 'generated';
  seed_origin: string;
  include: string;
  priority_hint: string;
  notes: string;
}

export interface VolumeResult {
  keyword: string;
  volume_avg_monthly: number | null;
  volume_range_raw: string | null;
  three_month_change: string | null;
  yoy_change: string | null;
  source: 'keyword_planner_browser';
  status: 'ok' | 'missing' | 'error';
  notes?: string;
}

export interface SearchResultEntry {
  title: string;
  url: string;
  snippet: string;
  domain: string;
}

export interface SerpAnalysis {
  keyword: string;
  dominant_intent: 'informational' | 'how-to' | 'commercial-investigation' | 'comparison' | 'mixed';
  top_result_types: string[];
  top_domains: string[];
  serp_features: string[];
  opportunity_summary: string;
  opportunity_score: number;
}

export interface KeywordAnalysis {
  keyword: string;
  seed_origin: string;
  source: 'manual' | 'generated';
  include: boolean;
  priority_hint?: string;
  notes?: string;
  volume_avg_monthly: number | null;
  volume_range_raw: string | null;
  three_month_change: string | null;
  yoy_change: string | null;
  volume_source: string;
  intent: SerpAnalysis['dominant_intent'];
  serp_features: string[];
  top_result_types: string[];
  top_domains: string[];
  site_fit_score: number;
  opportunity_score: number;
  service_adjacency_score: number;
  demand_score: number;
  recommendation: 'target now' | 'cluster later' | 'skip' | 'manual review';
  recommended_content_type:
    | 'glossary'
    | 'how-to'
    | 'comparison'
    | 'checklist/template'
    | 'opinionated expert article'
    | 'pillar article';
  reasoning: string;
  confidence: 'low' | 'medium' | 'high';
}

export interface RunPaths {
  runDir: string;
  expandedCsvPath: string;
  reviewCsvPath: string;
  volumeJsonPath: string;
  serpJsonPath: string;
  analysisJsonPath: string;
  reportPath: string;
}
