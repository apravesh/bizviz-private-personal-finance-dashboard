import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { assetManifestPlugin } from './asset-manifest-plugin';

function run(files: string[], outDir: string) {
  const plugin = assetManifestPlugin() as any;
  plugin.configResolved({ root: '/', build: { outDir } });
  const emitted: any[] = [];
  const bundle = Object.fromEntries(files.map((f) => [f, {}]));
  plugin.generateBundle.call({ emitFile: (f: any) => emitted.push(f) }, {}, bundle);
  plugin.closeBundle();
  return JSON.parse(emitted[0].source) as { build_id: string; assets: string[] };
}

describe('assetManifestPlugin', () => {
  it('lists hashed assets (no html or maps) and stamps the build id into sw.js', () => {
    const dir = mkdtempSync(join(tmpdir(), 'bizviz-sw-'));
    writeFileSync(join(dir, 'sw.js'), "const BUILD_ID = '__BUILD_ID__'; // __BUILD_ID__");
    const manifest = run(['index.html', 'assets/a-1.js', 'assets/a-1.js.map', 'assets/b-2.css'], dir);
    expect(manifest.assets).toEqual(['/assets/a-1.js', '/assets/b-2.css']);
    const sw = readFileSync(join(dir, 'sw.js'), 'utf-8');
    expect(sw).not.toContain('__BUILD_ID__');
    expect(sw).toContain(manifest.build_id);
  });

  it('changes the build id when the assets change', () => {
    const dir = mkdtempSync(join(tmpdir(), 'bizviz-sw-'));
    const a = run(['assets/a-1.js'], dir);
    const b = run(['assets/a-2.js'], dir);
    expect(a.build_id).not.toBe(b.build_id);
  });

  it('does nothing when there is no sw.js', () => {
    const dir = mkdtempSync(join(tmpdir(), 'bizviz-sw-'));
    expect(() => run(['assets/a.js'], dir)).not.toThrow();
  });
});
