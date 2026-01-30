import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const uiDir = path.resolve(__dirname, "..");
const distHtml = path.resolve(uiDir, "dist", "index.html");
const outDir = path.resolve(uiDir, "..", "static");
const outHtml = path.resolve(outDir, "webui.html");

if (!fs.existsSync(distHtml)) {
  console.error(`missing build output: ${distHtml}`);
  process.exit(1);
}

fs.mkdirSync(outDir, { recursive: true });
fs.copyFileSync(distHtml, outHtml);
console.log(`exported: ${outHtml}`);

