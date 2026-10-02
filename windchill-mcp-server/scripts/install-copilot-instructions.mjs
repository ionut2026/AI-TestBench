// Installs the RV&S agent playbook as a global Copilot CLI instructions file, so every Copilot session
// (in any folder) knows how to search Windchill RV&S autonomously.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { INSTRUCTIONS } from "../src/instructions.js";

const dir = path.join(os.homedir(), ".copilot", "instructions");
const file = path.join(dir, "windchill.instructions.md");
fs.mkdirSync(dir, { recursive: true });
fs.rmSync(path.join(dir, "windchill-rvs.instructions.md"), { force: true }); // file name used before the rename
fs.writeFileSync(file, `---\napplyTo: "**"\n---\n${INSTRUCTIONS}`);
console.log(`Installed ${file}`);
