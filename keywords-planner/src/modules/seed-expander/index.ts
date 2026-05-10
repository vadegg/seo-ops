import { expandSeedKeywords } from '../../lib/keywords.js';
import type { ModifiersConfig, SeedKeyword } from '../../types/index.js';

export function expandSeeds(seeds: string[], modifiers: ModifiersConfig): SeedKeyword[] {
  return expandSeedKeywords(seeds, modifiers);
}
