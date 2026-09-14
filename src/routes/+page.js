// Shell is fully client-rendered (roster data is per-user search results);
// the page itself carries nothing worth pre-rendering or SSR-ing.
export const prerender = true;
export const ssr = false;
