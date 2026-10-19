import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config.ts';

// Components must resolve Svelte's browser build so mount() works under jsdom.
export default mergeConfig(viteConfig, defineConfig({ resolve: { conditions: ['browser'] } }));
