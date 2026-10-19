/// <reference types="vitest/config" />
import { execSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig, type Plugin } from 'vite';

const here = dirname(fileURLToPath(import.meta.url));

function computeAppVersion(): string {
  const pyproject = readFileSync(resolve(here, '../pyproject.toml'), 'utf-8');
  const version = /^version\s*=\s*"([^"]+)"/m.exec(pyproject)?.[1] ?? '0.0.0';
  let hash = 'nogit';
  try {
    hash =
      execSync('git rev-parse --short HEAD', { cwd: here, stdio: ['ignore', 'pipe', 'ignore'] })
        .toString()
        .trim() || 'nogit';
  } catch {
    hash = 'nogit';
  }
  return `${version}+${hash}`;
}

const appVersion = computeAppVersion();

function bizvizBuildPlugin(): Plugin {
  let outDir = 'dist';
  return {
    name: 'bizviz-build',
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    transformIndexHtml: {
      order: 'post',
      handler(html) {
        return html.replace(/<script\b([^>]*)>/g, (tag, attrs: string) =>
          /\snonce\s*=/.test(attrs) ? tag : `<script${attrs} nonce="__CSP_NONCE__">`,
        );
      },
    },
    closeBundle() {
      mkdirSync(outDir, { recursive: true });
      writeFileSync(resolve(outDir, 'version.json'), JSON.stringify({ app_version: appVersion }));
    },
  };
}

export default defineConfig({
  plugins: [svelte(), bizvizBuildPlugin()],
  define: { __APP_VERSION__: JSON.stringify(appVersion) },
  build: { outDir: 'dist', emptyOutDir: true },
  test: { environment: 'jsdom' },
});
