#!/usr/bin/env node
/**
 * Per-brand local dev launcher (2026-11-06).
 *
 * Real bug this replaces: the original `dev:carenest`/`dev:stylenest` npm
 * scripts copied a brand's own `.env.local.<brand>` file *over* the shared
 * `.env.local` before starting `next dev`. That worked for a single running
 * instance, but Next.js's dev server actively watches `.env.local` on disk
 * and hot-reloads its env values on change (visible as its own "Reload env:
 * .env.local" log line) — so starting a SECOND brand's dev server overwrote
 * that one shared file, and the FIRST, already-running server picked up the
 * same change and silently switched to the second brand's config too.
 * Confirmed live: both localhost:3003 and :3007 rendered as StyleNest.
 *
 * This script reads the brand's env file directly and passes it only into
 * the memory of the one `next dev` child process it spawns — nothing is
 * ever written to `.env.local` (or any shared file) again, so two brands'
 * dev servers can run at the same time with zero chance of one clobbering
 * the other's environment.
 */

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const [, , brand, port] = process.argv;
if (!brand || !port) {
  console.error('Usage: node scripts/dev.js <brand> <port>');
  process.exit(1);
}

const envFilePath = path.join(__dirname, '..', `.env.local.${brand}`);
let envFileContents;
try {
  envFileContents = fs.readFileSync(envFilePath, 'utf8');
} catch {
  console.error(`Missing env file: ${envFilePath}`);
  process.exit(1);
}

const brandEnv = {};
for (const line of envFileContents.split('\n')) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith('#')) continue;
  const eq = trimmed.indexOf('=');
  if (eq === -1) continue;
  brandEnv[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
}

// `node <resolved next CLI path>` instead of `npx next` — npx failed to
// resolve on this machine's PATH when spawned from inside this script
// (confirmed live), even though `node` itself (already running this very
// file) obviously works. `require.resolve` finds next's real CLI entry
// point directly from node_modules, sidestepping PATH/shim resolution
// entirely — there's no PATH lookup left to fail.
const nextBin = require.resolve('next/dist/bin/next');

const result = spawnSync(process.execPath, [nextBin, 'dev', '-p', port], {
  stdio: 'inherit',
  cwd: path.join(__dirname, '..'),
  env: { ...process.env, ...brandEnv },
});
process.exit(result.status ?? 1);
