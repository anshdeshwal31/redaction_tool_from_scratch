// Runs the Next.js CLI with telemetry disabled (plan §2.4). No other behaviour.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");
const env = { ...process.env, NEXT_TELEMETRY_DISABLED: "1", DO_NOT_TRACK: "1" };
const r = spawnSync(process.execPath, [nextBin, ...process.argv.slice(2)], { stdio: "inherit", env });
process.exit(r.status ?? 1);
