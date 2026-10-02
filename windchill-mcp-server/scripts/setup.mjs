// One-command setup of the Windchill RV&S MCP server on a Windows machine:
// checks prerequisites, installs dependencies, compiles the Java bridge, tests the RV&S connection and
// registers the server with Copilot CLI and VS Code. Uses only Node built-ins so it runs before 'npm install'.
// Usage: setup.cmd [--host alm.stratec.com] [--port 7001] [--client-home "C:\...\ILMClient13"]
//                  [--no-cli] [--no-vscode] [--no-instructions] [--skip-test] [--help]
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SERVER = path.join(ROOT, "src", "server.js");
const NAME = "windchill";
const LEGACY_NAMES = ["windchill-rvs"]; // removed from configs when found
const NODE = process.execPath; // full path, so Copilot/VS Code don't depend on PATH
const DEFAULT_CLIENT = "C:\\Program Files\\Integrity\\ILMClient13";

const HELP = `Windchill RV&S MCP server setup

  setup.cmd [options]          (or: node scripts\\setup.mjs [options])

Options:
  --host <name>          RV&S server host        (default: $RVS_HOSTNAME or alm.stratec.com)
  --port <n>             RV&S server port        (default: $RVS_PORT or 7001)
  --client-home <dir>    RV&S client folder      (default: auto-detected)
  --no-cli               don't register with GitHub Copilot CLI
  --no-vscode            don't register with VS Code
  --no-instructions      don't install the Copilot search playbook
  --skip-test            don't test the RV&S connection
  --help                 show this help
`;

// ---------- arguments ----------
const args = { cli: true, vscode: true, instructions: true, test: true };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  const val = () => {
    const v = argv[++i];
    if (v === undefined || v.startsWith("--")) fail(`Missing value for ${a}.`);
    return v;
  };
  if (a === "--help" || a === "-h" || a === "/?") {
    console.log(HELP);
    process.exit(0);
  } else if (a === "--host") args.host = val();
  else if (a === "--port") args.port = val();
  else if (a === "--client-home") args.clientHome = val();
  else if (a === "--no-cli") args.cli = false;
  else if (a === "--no-vscode") args.vscode = false;
  else if (a === "--no-instructions") args.instructions = false;
  else if (a === "--skip-test") args.test = false;
  else fail(`Unknown option '${a}'.\n\n${HELP}`);
}
const host = args.host || process.env.RVS_HOSTNAME || "alm.stratec.com";
const port = String(args.port || process.env.RVS_PORT || "7001");
if (!/^\d+$/.test(port)) fail(`Invalid port '${port}'.`);

// ---------- helpers ----------
const warnings = [];
let stepNo = 0;
const TOTAL = 7;
function step(title) {
  console.log(`\n[${++stepNo}/${TOTAL}] ${title}`);
}
const ok = (msg) => console.log(`      OK   ${msg}`);
const info = (msg) => console.log(`           ${msg}`);
function warn(msg) {
  warnings.push(msg);
  console.log(`      WARN ${msg}`);
}
function fail(msg) {
  console.error(`\nSETUP FAILED: ${msg}`);
  process.exit(1);
}
function readJson(file) {
  if (!fs.existsSync(file)) return {};
  const text = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "");
  if (!text.trim()) return {};
  return JSON.parse(text); // throws on JSONC comments; caller handles it
}
function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  if (fs.existsSync(file)) fs.copyFileSync(file, `${file}.bak`);
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n");
}

console.log("Windchill RV&S MCP server setup");
console.log(`  folder : ${ROOT}`);
console.log(`  server : ${host}:${port}`);
if (process.platform !== "win32") fail("This setup supports Windows only (the RV&S client and its Java API are Windows installs).");

// ---------- 1. Node ----------
step("Checking Node.js");
const major = Number(process.versions.node.split(".")[0]);
if (major < 18) fail(`Node.js ${process.versions.node} is too old; install Node.js 18 or newer (e.g. 'winget install OpenJS.NodeJS.LTS').`);
ok(`Node.js ${process.versions.node}`);

// ---------- 2. RV&S client ----------
step("Locating the Windchill RV&S client");
const isClient = (dir) =>
  dir && fs.existsSync(path.join(dir, "jre", "bin", "java.exe")) && fs.existsSync(path.join(dir, "lib", "mksapi.jar"));
let clientHome = args.clientHome || process.env.RVS_CLIENT_HOME;
if (clientHome) {
  if (!isClient(clientHome)) fail(`'${clientHome}' is not an RV&S client folder (expected jre\\bin\\java.exe and lib\\mksapi.jar).`);
} else {
  const roots = [process.env.ProgramFiles, process.env["ProgramFiles(x86)"], "C:\\Program Files", "C:\\Program Files (x86)"].filter(Boolean);
  const candidates = [DEFAULT_CLIENT];
  for (const r of [...new Set(roots)]) {
    for (const vendor of ["Integrity", "PTC", "MKS"]) {
      const base = path.join(r, vendor);
      if (!fs.existsSync(base)) continue;
      for (const d of fs.readdirSync(base)) candidates.push(path.join(base, d));
    }
  }
  clientHome = candidates.find(isClient);
  if (!clientHome) {
    fail(
      "No Windchill RV&S client found (looked for jre\\bin\\java.exe and lib\\mksapi.jar under Program Files\\Integrity, PTC, MKS).\n" +
        "Install the RV&S client, or pass its folder with --client-home \"<folder>\".",
    );
  }
}
ok(clientHome);
const env = { ...process.env, RVS_CLIENT_HOME: clientHome, RVS_HOSTNAME: host, RVS_PORT: port };

// ---------- 3. npm install (also compiles the Java bridge via postinstall) ----------
step("Installing dependencies and compiling the Java bridge (npm install)");
const npm = spawnSync("npm install --no-audit --no-fund --loglevel=error", { cwd: ROOT, env, stdio: "inherit", shell: true });
if (npm.status !== 0) fail("'npm install' failed (see the output above). Check network/proxy access to the npm registry.");
if (!fs.existsSync(path.join(ROOT, "build", "classes", "RvsBridge.class"))) fail("Java bridge was not compiled (build\\classes\\RvsBridge.class missing).");
ok("dependencies installed, bridge compiled to build\\classes");

// ---------- 4. Copilot CLI ----------
step("Registering with GitHub Copilot CLI");
const serverEnv = { RVS_HOSTNAME: host, RVS_PORT: port };
if (path.resolve(clientHome).toLowerCase() !== DEFAULT_CLIENT.toLowerCase()) serverEnv.RVS_CLIENT_HOME = clientHome;
if (!args.cli) info("skipped (--no-cli)");
else {
  const file = path.join(os.homedir(), ".copilot", "mcp-config.json");
  try {
    const cfg = readJson(file);
    cfg.mcpServers ??= {};
    for (const old of LEGACY_NAMES) delete cfg.mcpServers[old];
    cfg.mcpServers[NAME] = { type: "local", command: NODE, args: [SERVER], env: serverEnv, tools: ["*"] };
    writeJson(file, cfg);
    ok(`${file} (server '${NAME}')`);
  } catch (e) {
    warn(`Could not update ${file}: ${e.message}. Add the server manually (see README, 'Manual registration').`);
  }
}

// ---------- 5. VS Code ----------
step("Registering with VS Code");
if (!args.vscode) info("skipped (--no-vscode)");
else {
  const appData = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
  const userDirs = ["Code", "Code - Insiders"].map((d) => path.join(appData, d, "User")).filter((d) => fs.existsSync(d));
  if (!userDirs.length) info("VS Code not found for this user; skipped");
  for (const dir of userDirs) {
    const file = path.join(dir, "mcp.json");
    try {
      const cfg = readJson(file);
      cfg.servers ??= {};
      for (const old of LEGACY_NAMES) delete cfg.servers[old];
      cfg.servers[NAME] = { type: "stdio", command: NODE, args: [SERVER], env: serverEnv };
      writeJson(file, cfg);
      ok(`${file} (server '${NAME}')`);
    } catch (e) {
      warn(`Could not update ${file} (${e.message}; comments in the file?). Add the server manually (see README, 'Manual registration').`);
    }
  }
}

// ---------- 6. Copilot instructions ----------
step("Installing the Copilot search playbook");
if (!args.instructions) info("skipped (--no-instructions)");
else {
  const r = spawnSync(process.execPath, [path.join(ROOT, "scripts", "install-copilot-instructions.mjs")], { cwd: ROOT, env, encoding: "utf8" });
  if (r.status === 0) ok(r.stdout.trim().replace(/^Installed /, ""));
  else warn(`Could not install the playbook: ${r.stderr || r.stdout}`);
}

// ---------- 7. Connection test ----------
step(`Testing the connection to ${host}:${port}`);
if (!args.test) info("skipped (--skip-test)");
else {
  info("If the RV&S client asks you to log in, log in with your own Integrity user.");
  Object.assign(process.env, env);
  const { bridge } = await import(pathToFileURL(path.join(ROOT, "src", "bridge.js")).href);
  const withTimeout = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(`no answer within ${ms / 1000} s`)), ms))]);
  try {
    const about = (await withTimeout(bridge.run({ cmd: "about" }), 120000)).workItems[0]?.fields || {};
    const types = await withTimeout(bridge.run({ cmd: "types" }), 300000);
    const servers = (await withTimeout(bridge.run({ cmd: "servers" }), 60000)).workItems.map((w) => w.fields);
    const conn = servers.find((s) => s.default) || servers[0] || {};
    ok(`${about.title || "Windchill RV&S"} ${about.version || ""} (API ${about.apiversion || "?"})`);
    ok(`connected to ${conn.hostname || host}:${conn.portnumber || port} as Integrity user '${conn.username || "?"}'`);
    ok(`${types.workItems.length} item types visible`);
  } catch (e) {
    warn(
      `Connection test failed: ${e.message.split("\n")[0]}\n` +
        "           Start the RV&S client, log in once to the server, then re-run setup (or: setup.cmd --skip-test).",
    );
  } finally {
    bridge.stop();
  }
}

// ---------- summary ----------
console.log(`\n${warnings.length ? `Setup finished with ${warnings.length} warning(s):` : "Setup finished successfully."}`);
for (const w of warnings) console.log(`  - ${w}`);
console.log(`
Next steps:
  1. Restart GitHub Copilot CLI (type /restart, or exit and run 'copilot') and/or VS Code
     (Command Palette > "MCP: List Servers" > ${NAME} > Restart).
  2. Check with /mcp that '${NAME}' is connected.
  3. Ask, for example: "Is the windchill MCP server OK?" or "List all Draft user stories for PRA in Replicant".
`);
process.exit(warnings.length ? 2 : 0);
