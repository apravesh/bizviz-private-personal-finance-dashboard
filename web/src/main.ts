import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
import { APP_VERSION } from './lib/version';

const app = mount(App, { target: document.getElementById('app')! });

if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js?v=' + encodeURIComponent(APP_VERSION)).catch(() => undefined);
}

export default app;
