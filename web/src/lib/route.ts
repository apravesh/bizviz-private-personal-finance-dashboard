import { readable } from 'svelte/store';

export type RouteName = 'dashboard' | 'investments' | 'transactions' | 'settings';

export interface Route {
  name: RouteName;
  params: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#/, '').split('?');
  const name: RouteName =
    path === '/investments'
      ? 'investments'
      : path === '/transactions'
        ? 'transactions'
        : path === '/settings'
          ? 'settings'
          : 'dashboard';
  return { name, params: new URLSearchParams(query) };
}

export const route = readable<Route>(parseHash(location.hash), (set) => {
  const update = () => set(parseHash(location.hash));
  window.addEventListener('hashchange', update);
  update();
  return () => window.removeEventListener('hashchange', update);
});
