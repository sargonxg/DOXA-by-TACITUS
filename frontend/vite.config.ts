import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import packageInfo from './package.json';

export default defineConfig({
  plugins: [react(), { name: 'doxa-build-identity', generateBundle() {
    this.emitFile({ type: 'asset', fileName: 'doxa-build.json', source: JSON.stringify({ version: packageInfo.version }) });
  } }],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' }, port: 5173 },
  build: { outDir: 'dist', sourcemap: false },
});
