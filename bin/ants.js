#!/usr/bin/env node
// Launcher: ensure a private Python (>=3.11) virtualenv holds ants-lab, then run `ants` with the same arguments.
// Nothing is installed globally; the venv lives in ~/.ants/venv-<version> (override with ANTS_HOME).
"use strict";
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const pkgDir = path.resolve(__dirname, "..");
const version = require(path.join(pkgDir, "package.json")).version;
const home = process.env.ANTS_HOME || path.join(os.homedir(), ".ants");
const venv = path.join(home, `venv-${version}`);
const bin = path.join(venv, process.platform === "win32" ? "Scripts" : "bin");
const exe = (n) => path.join(bin, process.platform === "win32" ? `${n}.exe` : n);

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, { stdio: "inherit", ...opts });
}

function findPython() {
  const names = [process.env.ANTS_PYTHON, "python3.13", "python3.12", "python3.11", "python3", "python"].filter(Boolean);
  for (const n of names) {
    const r = spawnSync(n, ["-c", "import sys;print(sys.version_info>=(3,11))"], { encoding: "utf8" });
    if (r.status === 0 && r.stdout.trim() === "True") return n;
  }
  return null;
}

function bootstrap() {
  const py = findPython();
  if (!py) {
    console.error("ants needs Python >= 3.11 on PATH (or set ANTS_PYTHON). See https://www.python.org/downloads/");
    process.exit(127);
  }
  fs.mkdirSync(home, { recursive: true });
  console.error(`ants: first run, creating ${venv} (one time)`);
  let r = run(py, ["-m", "venv", venv]);
  if (r.status !== 0) process.exit(r.status || 1);
  // build from a throw-away copy: pip must not write build/ or egg-info into the (possibly read-only) package directory
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ants-src-"));
  // filter on the path inside the package: an installed package itself lives under node_modules/
  const skip = /(^|[\\/])(node_modules|build|dist|runs|internal|\.venv|\.git|__pycache__|raw|processed)([\\/]|$)/;
  fs.cpSync(pkgDir, tmp, { recursive: true, filter: (p) => !skip.test(path.relative(pkgDir, p)) });
  r = run(exe("python"), ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", `${tmp}[gyms]`]);
  fs.rmSync(tmp, { recursive: true, force: true });
  if (r.status !== 0) {
    fs.rmSync(venv, { recursive: true, force: true });   // never leave a half-built environment behind
    process.exit(r.status || 1);
  }
}

if (!fs.existsSync(exe("ants"))) bootstrap();

// ANTS_ROOT tells the Python side where the bundled contracts and data catalog live (this package directory).
// Signals are forwarded so `kill` or Ctrl-C on the launcher reaches the daemon and it can stop cleanly.
const { spawn } = require("node:child_process");
const child = spawn(exe("ants"), process.argv.slice(2), { stdio: "inherit", env: { ...process.env, ANTS_ROOT: pkgDir } });
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(sig, () => child.kill(sig));
child.on("error", (e) => { console.error(`ants: could not start ${exe("ants")}: ${e.message}`); process.exit(1); });
child.on("exit", (code, signal) => process.exit(code !== null ? code : 128 + (os.constants.signals[signal] || 0)));
