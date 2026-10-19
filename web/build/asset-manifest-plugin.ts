import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Plugin } from 'vite';

export const BUILD_ID_PLACEHOLDER = '__BUILD_ID__';

export function assetManifestPlugin(): Plugin {
  let outDir = 'dist';
  let buildId = '';
  return {
    name: 'bizviz-asset-manifest',
    apply: 'build',
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    generateBundle(_options, bundle) {
      const assets = Object.keys(bundle)
        .filter((name) => !name.endsWith('.map') && !name.endsWith('.html'))
        .map((name) => `/${name}`)
        .sort();
      buildId = createHash('sha256').update(assets.join('\n')).digest('hex').slice(0, 12);
      this.emitFile({
        type: 'asset',
        fileName: 'asset-manifest.json',
        source: JSON.stringify({ build_id: buildId, assets }),
      });
    },
    closeBundle() {
      const path = resolve(outDir, 'sw.js');
      try {
        const source = readFileSync(path, 'utf-8');
        writeFileSync(path, source.replaceAll(BUILD_ID_PLACEHOLDER, buildId));
      } catch {
        // no service worker in this build output
      }
    },
  };
}
