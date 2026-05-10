import test from 'node:test';
import assert from 'node:assert/strict';
import { expandSeedKeywords } from '../src/lib/keywords.js';
test('expandSeedKeywords keeps manual seeds and generates narrowed variants without duplicates', () => {
    const modifiers = {
        generic_prefixes: ['how to'],
        generic_suffixes: ['template'],
        audience_suffixes: ['for founders'],
        intent_suffixes: ['process']
    };
    const results = expandSeedKeywords(['Product Research', 'Product Research'], modifiers);
    const keywords = results.map((entry) => entry.keyword);
    assert.ok(keywords.includes('product research'));
    assert.ok(keywords.includes('how to product research'));
    assert.ok(keywords.includes('product research template'));
    assert.equal(keywords.filter((keyword) => keyword === 'product research').length, 1);
});
