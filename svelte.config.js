import adapterNode from '@sveltejs/adapter-node';
import adapterVercel from '@sveltejs/adapter-vercel';

// One source, two builds: `ADAPTER=node pnpm build` ships the box-ready
// bundle (deploy.sh → Hetzner), plain `pnpm build` ships Vercel (default,
// keeps `vercel deploy`/CI working untouched).
const adapter = process.env.ADAPTER === 'node' ? adapterNode() : adapterVercel();

/** @type {import('@sveltejs/kit').Config} */
const config = {
  kit: {
    adapter
  }
};

export default config;
