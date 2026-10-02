import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export const config = {
  clientHome: process.env.RVS_CLIENT_HOME || "C:\\Program Files\\Integrity\\ILMClient13",
  hostname: process.env.RVS_HOSTNAME || "",
  port: process.env.RVS_PORT || "",
  timeoutMs: Number(process.env.RVS_TIMEOUT_MS || 180000),
};

const javaExe = () => path.join(config.clientHome, "jre", "bin", "java.exe");
const apiJar = () => path.join(config.clientHome, "lib", "mksapi.jar");
const classesDir = path.join(ROOT, "build", "classes");
const javaSrcDir = path.join(ROOT, "java");

/** Compiles the Java bridge with the RV&S client's bundled JRE (it ships jdk.compiler). */
export function compileBridge({ force = false } = {}) {
  const src = path.join(javaSrcDir, "RvsBridge.java");
  const cls = path.join(classesDir, "RvsBridge.class");
  if (!force && fs.existsSync(cls) && fs.statSync(cls).mtimeMs >= fs.statSync(src).mtimeMs) return;
  for (const f of [javaExe(), apiJar()]) {
    if (!fs.existsSync(f)) throw new Error(`Not found: ${f}. Set RVS_CLIENT_HOME to your Windchill RV&S client folder.`);
  }
  fs.mkdirSync(classesDir, { recursive: true });
  const r = spawnSync(javaExe(), [path.join(javaSrcDir, "Compile.java"), "-cp", apiJar(), "-d", classesDir, "-Xlint:none", src], {
    encoding: "utf8",
  });
  if (r.status !== 0) throw new Error(`Failed to compile RvsBridge.java:\n${r.stderr || r.stdout || r.error}`);
}

class Bridge {
  #proc = null;
  #pending = new Map();
  #nextId = 1;
  #stderrTail = [];

  #start() {
    compileBridge();
    const env = { ...process.env, RVS_HOSTNAME: config.hostname, RVS_PORT: String(config.port) };
    const proc = spawn(javaExe(), ["-Dfile.encoding=UTF-8", "-cp", `${classesDir}${path.delimiter}${apiJar()}`, "RvsBridge"], {
      env,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    proc.stdout.setEncoding("utf8");
    proc.stderr.setEncoding("utf8");
    readline.createInterface({ input: proc.stdout }).on("line", (line) => this.#onLine(line));
    readline.createInterface({ input: proc.stderr }).on("line", (line) => {
      this.#stderrTail.push(line);
      if (this.#stderrTail.length > 20) this.#stderrTail.shift();
      process.stderr.write(`[rvs-bridge] ${line}\n`);
    });
    const fail = (why) => {
      if (this.#proc !== proc) return;
      this.#proc = null;
      const detail = this.#stderrTail.slice(-5).join("\n");
      for (const { reject, timer } of this.#pending.values()) {
        clearTimeout(timer);
        reject(new Error(`RV&S bridge ${why}${detail ? `:\n${detail}` : ""}`));
      }
      this.#pending.clear();
    };
    proc.on("exit", (code) => fail(`exited (code ${code})`));
    proc.on("error", (err) => fail(`failed to start: ${err.message}`));
    this.#proc = proc;
  }

  #onLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      process.stderr.write(`[rvs-bridge] unexpected output: ${line}\n`);
      return;
    }
    const p = this.#pending.get(msg.id);
    if (!p) return;
    this.#pending.delete(msg.id);
    clearTimeout(p.timer);
    if (msg.ok) p.resolve(msg.result);
    else p.reject(new Error(msg.error || "Unknown RV&S error"));
  }

  /**
   * Executes an RV&S API command.
   * @param {{app?: string, cmd: string, options?: Array<[string, string?]>, selection?: string[], limit?: number, maxFieldLength?: number}} req
   */
  run(req) {
    if (!this.#proc) this.#start();
    const id = this.#nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new Error(`RV&S command '${req.app || "im"} ${req.cmd}' timed out after ${config.timeoutMs} ms`));
      }, config.timeoutMs);
      this.#pending.set(id, { resolve, reject, timer });
      this.#proc.stdin.write(JSON.stringify({ id, app: "im", ...req }) + "\n");
    });
  }

  stop() {
    if (this.#proc) {
      this.#proc.stdin.end();
      this.#proc = null;
    }
  }
}

export const bridge = new Bridge();
