/** Bounded JSON-in/JSON-out validator using the same installed engine as review. */
import { createHash } from "node:crypto";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const INPUT_LIMIT = 16 * 1024 * 1024;
const chunks = [];
let size = 0;
for await (const chunk of process.stdin) {
  size += chunk.length;
  if (size > INPUT_LIMIT) throw new Error("KaTeX request exceeds input limit");
  chunks.push(chunk);
}
const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const katex = require(process.env.AEGIS_KATEX_MODULE || "katex");
if (katex.version !== request.expected_version) {
  process.stdout.write(JSON.stringify({
    status: "unavailable", version: katex.version,
    message: `Expected KaTeX ${request.expected_version}; found ${katex.version}`,
    results: [],
  }));
  process.exit(0);
}
if (!Array.isArray(request.expressions) || request.expressions.length > 10000) {
  throw new Error("Invalid or excessive KaTeX expression request");
}
const results = [];
// Warnings are recorded below; avoid an unbounded duplicate stderr stream.
console.warn = () => {};
for (const entry of request.expressions) {
  const warnings = [];
  const warningKeys = new Set();
  let warningCount = 0;
  const deniedCommands = new Set();
  try {
    if (typeof entry.latex !== "string" || entry.latex.length > 32768) {
      throw new Error("Invalid or excessive KaTeX expression size");
    }
    const rendered = katex.renderToString(entry.latex, {
      throwOnError: true,
      // This is trust:false with an observation receipt. KaTeX can render a
      // denied command in red without throwing; observing the parser's trust
      // callback detects that refusal without guessing from a command regex
      // or rejecting a legitimate user-authored red expression.
      trust: (context) => {
        deniedCommands.add(context.command);
        return false;
      },
      strict: (code, message) => {
        warningCount += 1;
        const key = `${code}:${message}`;
        if (!warningKeys.has(key) && warnings.length < 2) {
          warningKeys.add(key);
          warnings.push({ code: String(code).slice(0, 64), message: String(message).slice(0, 100) });
        }
        return "warn";
      },
      output: "htmlAndMathml",
      maxSize: 20,
      maxExpand: 1000,
      macros: {},
    });
    results.push({
      id: entry.id,
      ok: deniedCommands.size === 0,
      code: deniedCommands.size ? "katex_untrusted_command" : null,
      message: deniedCommands.size
        ? `Target renderer refuses: ${[...deniedCommands].join(", ")}`.slice(0, 240) : "",
      rendered_sha256: createHash("sha256").update(rendered).digest("hex"),
      warnings, warning_count: warningCount,
    });
  } catch (error) {
    results.push({
      id: entry.id, ok: false, code: "katex_render_invalid",
      message: String(error.message || error).slice(0, 240), warnings,
      warning_count: warningCount,
    });
  }
}
process.stdout.write(JSON.stringify({ status: "checked", version: katex.version, results }));
