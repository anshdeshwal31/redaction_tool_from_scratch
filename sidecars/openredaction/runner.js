// OpenRedaction sidecar (plan §11 C1). Reads {"options": {...}, "texts": [...]} on stdin and writes
// {"engine": ..., "results": [[{type, start, end, confidence, severity}]]} on stdout: offsets only,
// never the detected values. Offsets are UTF-16 code units (JavaScript); the Python adapter converts.
//
// Safety: a network guard is installed before the library loads (sockets, TLS, HTTP(S), DNS, fetch all
// throw), the library's learning store (which would write .openredaction/learnings.json) is forced
// off, as are caching, audit logging, metrics and NER. The hosted AI assist is not part of this
// package version and is never configured. No PII in this file.
"use strict";

const blocked = (what) => () => { throw new Error(`network blocked in the OpenRedaction sidecar: ${what}`); };
const net = require("net");
const tls = require("tls");
const http = require("http");
const https = require("https");
const dns = require("dns");
net.Socket.prototype.connect = blocked("net.Socket.connect");
net.connect = net.createConnection = blocked("net.connect");
tls.connect = blocked("tls.connect");
for (const m of [http, https]) { m.request = blocked("http.request"); m.get = blocked("http.get"); }
dns.lookup = dns.resolve = blocked("dns");
if (dns.promises) { dns.promises.lookup = dns.promises.resolve = blocked("dns.promises"); }
globalThis.fetch = blocked("fetch");

const FORCED = {
  enableLearning: false, enableCache: false, enableAuditLog: false, enableMetrics: false, enableRBAC: false,
  enableNER: false, enablePriorityOptimization: false, debug: false, deterministic: true,
};

async function selftest() {
  // proves the guard: every outbound path must throw before any library code runs
  const tries = {
    fetch: () => fetch("https://example.com"),
    socket: () => new net.Socket().connect(443, "example.com"),
    https: () => https.get("https://example.com"),
    dns: () => dns.lookup("example.com", () => {}),
  };
  const blockedAll = {};
  for (const [k, f] of Object.entries(tries)) {
    try { await f(); blockedAll[k] = false; } catch (e) { blockedAll[k] = /network blocked/.test(String(e && e.message)); }
  }
  process.stdout.write(JSON.stringify({ blocked: blockedAll }));
}

async function main() {
  if (process.argv.includes("--selftest-network")) return selftest();
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const req = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const { OpenRedaction } = require("openredaction");
  const version = JSON.parse(require("fs").readFileSync(require("path").join(__dirname, "node_modules", "openredaction", "package.json"), "utf8")).version;
  const options = { ...(req.options || {}), ...FORCED };
  const detector = new OpenRedaction(options);
  const results = [];
  for (const text of req.texts) {
    const r = await detector.detect(text);
    results.push(r.detections.map((d) => ({
      type: d.type, start: d.position[0], end: d.position[1],
      confidence: typeof d.confidence === "number" ? Math.round(d.confidence * 10000) / 10000 : null,
      severity: d.severity,
    })));
  }
  process.stdout.write(JSON.stringify({ engine: { name: "openredaction", version, node: process.version, options }, results }));
}

main().catch((e) => {
  // the error class and message only; never the input
  process.stderr.write(`sidecar error: ${e && e.name}\n`);
  process.exit(2);
});
