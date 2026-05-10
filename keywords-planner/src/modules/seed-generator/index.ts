export interface SeedKeyword {
  keyword: string;
  source: 'manual' | 'generated';
}

// Placeholder for Module 0. v1 starts from input/seeds.yaml instead.
export function generateSeeds(): SeedKeyword[] {
  return [];
}
