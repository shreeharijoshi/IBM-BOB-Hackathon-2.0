/**
 * C++ Diagnostic — VS Code extension entry point.
 *
 * Provides commands:
 *   cppDiagnostic.diagnose         — Explain Error (Ctrl+Shift+D)
 *   cppDiagnostic.analyzeFile      — Analyze File (full multi-error panel)
 *   cppDiagnostic.checkCode        — Context Diagnostic: Check Code with Gemini (Ctrl+Shift+G)
 *   cppDiagnostic.clearDiagnostics — Clear all diagnostics and decorations
 *   cppDiagnostic.showPanel        — Show last explanation in a webview panel
 *
 * Accuracy & AI Features:
 *   - Accurate Root-Cause Location Resolution: separates GCC reported location
 *     from the actual mistake location in source code.
 *   - Precise token/range highlighting rather than highlighting full lines unnecessarily.
 *   - Gemini AI integration for complex errors and deep code reviews.
 *   - Cascade suppression and diagnostic deduplication.
 *   - Deterministic rules work 100% offline without any API key.
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as path from "path";
import * as http from "http";
import * as https from "https";
import { URL } from "url";

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------

let outputChannel: vscode.OutputChannel | undefined;
let diagnosticCollection: vscode.DiagnosticCollection | undefined;
let decorationType: vscode.TextEditorDecorationType | undefined;
let webviewPanel: vscode.WebviewPanel | undefined;

/** Maps file URI string → all fully-analysed diagnoses for that file */
const lastDiagnostics = new Map<string, DiagnosisWithRaw[]>();

/** Stores last Gemini code review result */
let lastReviewResult: GeminiReviewResult | undefined;

// Debounce timer for auto-analyze on save
let saveDebounceTimer: NodeJS.Timeout | undefined;

// ---------------------------------------------------------------------------
// Types mirroring backend output schema
// ---------------------------------------------------------------------------

interface LocationSpec {
  file?: string;
  line: number | null;
  column: number | null;
  end_line?: number | null;
  end_column?: number | null;
  source?: string;
}

interface Evidence {
  line: number | null;
  code: string;
}

interface RawError {
  file: string;
  line: number | null;
  column: number | null;
  severity: string;
  message: string;
}

interface Diagnosis {
  error_type: string;
  analysis_mode: string; // "deterministic" | "gemini" | "ai"
  confidence?: number;
  compiler_location?: LocationSpec;
  root_cause_location?: LocationSpec;
  compiler_message: string;
  compiler_explanation: string;
  source_explanation: string;
  explanation?: string;
  evidence: Evidence;
  what_to_check: string;
  suggestion: string;
}

/** Diagnosis with the original compiler diagnostic attached */
interface DiagnosisWithRaw extends Diagnosis {
  raw: RawError;
}

interface CleanResult {
  status: "clean";
  message?: string;
}

interface OkResult {
  status: "ok";
  diagnosis: DiagnosisWithRaw; // first error — backward compat
  diagnostics: DiagnosisWithRaw[]; // all errors — full multi-error
}

interface ErrorResult {
  status: "error";
  message: string;
}

type DiagnoseResult = CleanResult | OkResult | ErrorResult;

interface GeminiFinding {
  title: string;
  category: "bug" | "logic" | "runtime_risk" | "maintainability" | "improvement";
  severity?: "high" | "medium" | "low";
  line?: number | null;
  column?: number | null;
  explanation: string;
  suggestion: string;
}

interface GeminiReviewResult {
  status: "ok" | "disabled" | "error";
  title: string;
  scope?: string;
  file?: string;
  summary?: string;
  message?: string;
  findings: GeminiFinding[];
}

// ---------------------------------------------------------------------------
// Lazy accessors
// ---------------------------------------------------------------------------

function getChannel(): vscode.OutputChannel {
  if (!outputChannel) {
    outputChannel = vscode.window.createOutputChannel("C++ Diagnostic");
  }
  return outputChannel;
}

function getDiagnosticCollection(): vscode.DiagnosticCollection {
  if (!diagnosticCollection) {
    diagnosticCollection = vscode.languages.createDiagnosticCollection("cppDiagnostic");
  }
  return diagnosticCollection;
}

function getDecorationType(): vscode.TextEditorDecorationType {
  if (!decorationType) {
    decorationType = vscode.window.createTextEditorDecorationType({
      backgroundColor: new vscode.ThemeColor("diffEditor.insertedLineBackground"),
      isWholeLine: false,
      border: "1px solid rgba(239, 68, 68, 0.6)",
      borderRadius: "2px",
      overviewRulerColor: new vscode.ThemeColor("editorWarning.foreground"),
      overviewRulerLane: vscode.OverviewRulerLane.Right,
    });
  }
  return decorationType;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function repoRoot(context: vscode.ExtensionContext): string {
  const fs = require("fs") as typeof import("fs");
  const bundled = path.join(context.extensionPath, "backend", "diagnose.py");
  if (fs.existsSync(bundled)) {
    return context.extensionPath;
  }
  return path.resolve(context.extensionPath, "..");
}

function isCppFile(fileName: string): boolean {
  const ext = path.extname(fileName).toLowerCase();
  return [".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp"].includes(ext);
}

function clearAllMarkers(uri: vscode.Uri): void {
  getDiagnosticCollection().delete(uri);
  lastDiagnostics.delete(uri.toString());
  for (const editor of vscode.window.visibleTextEditors) {
    if (editor.document.uri.toString() === uri.toString()) {
      editor.setDecorations(getDecorationType(), []);
    }
  }
}

function getGeminiSettings(): {
  apiKey: string;
  model: string;
  enabled: boolean;
  timeout: number;
  preferGemini: boolean;
} {
  const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
  return {
    apiKey: cfg.get<string>("geminiApiKey")?.trim() ?? "",
    model: cfg.get<string>("geminiModel")?.trim() || "gemini-2.5-flash",
    enabled: cfg.get<boolean>("geminiEnabled") ?? true,
    timeout: cfg.get<number>("geminiTimeout") ?? 15,
    preferGemini: cfg.get<boolean>("preferGemini") ?? true,
  };
}

// ---------------------------------------------------------------------------
// Transport: HTTP backend
// ---------------------------------------------------------------------------

function httpRequest<T>(url: string, body: object): Promise<T> {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const isHttps = parsed.protocol === "https:";
    const agent = isHttps ? https : http;
    const data = JSON.stringify(body);

    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (isHttps ? 443 : 80),
      path: parsed.pathname + (parsed.search || ""),
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(data),
      },
    };

    const req = agent.request(options, (res) => {
      let raw = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        raw += chunk;
      });
      res.on("end", () => {
        try {
          resolve(JSON.parse(raw) as T);
        } catch {
          reject(new Error(`Backend returned non-JSON: ${raw.slice(0, 200)}`));
        }
      });
    });

    req.on("error", (err) => {
      reject(new Error(`Backend connection failed: ${err.message}`));
    });

    req.setTimeout(25000, () => {
      req.destroy();
      reject(new Error("Backend request timed out after 25 s."));
    });

    req.write(data);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Transport: Python CLI adapter
// ---------------------------------------------------------------------------

function runAdapter<T>(
  args: string[],
  rootDir: string,
  python: string
): Promise<T> {
  return new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";

    let proc: cp.ChildProcess;
    try {
      proc = cp.spawn(python, args, { cwd: rootDir });
    } catch (err) {
      reject(new Error(`Failed to start Python: ${(err as Error).message}`));
      return;
    }

    proc.stdout?.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    proc.stderr?.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    proc.on("error", (err: NodeJS.ErrnoException) => {
      if (err.code === "ENOENT") {
        reject(
          new Error(
            `Python interpreter not found: "${python}". Set cppDiagnostic.pythonPath in VS Code settings.`
          )
        );
      } else {
        reject(new Error(`Failed to start adapter: ${err.message}`));
      }
    });

    const timer = setTimeout(() => {
      proc.kill();
      reject(new Error("Diagnostic adapter timed out after 30 s."));
    }, 30000);

    proc.on("close", () => {
      clearTimeout(timer);
      const raw = stdout.trim();
      if (!raw) {
        const detail = stderr.trim() || "No output from adapter.";
        reject(new Error(`Adapter produced no output.\n    ${detail}`));
        return;
      }
      try {
        resolve(JSON.parse(raw) as T);
      } catch {
        reject(
          new Error(`Adapter returned non-JSON output:\n    ${raw.slice(0, 200)}`)
        );
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Transport dispatchers
// ---------------------------------------------------------------------------

async function fetchDiagnosis(
  filePath: string,
  sourceCode: string,
  context: vscode.ExtensionContext
): Promise<DiagnoseResult> {
  const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
  const backendUrl = cfg.get<string>("backendUrl")?.trim() ?? "";
  const gemini = getGeminiSettings();

  if (backendUrl) {
    try {
      return await httpRequest<DiagnoseResult>(`${backendUrl}/diagnose`, {
        source_code: sourceCode,
        compiler_output: "",
        gemini_api_key: gemini.apiKey || undefined,
        gemini_model: gemini.model,
        gemini_enabled: gemini.enabled,
        gemini_timeout: gemini.timeout,
        prefer_gemini: gemini.preferGemini && Boolean(gemini.apiKey),
      });
    } catch (err) {
      // Fallback to CLI if backend unavailable
      getChannel().appendLine(
        `Backend HTTP failed (${(err as Error).message}), falling back to Python CLI…`
      );
    }
  }

  const python = cfg.get<string>("pythonPath") ?? "python";
  const rootDir = repoRoot(context);
  const args = [
    path.join(rootDir, "backend", "diagnose.py"),
    "--file",
    filePath,
  ];

  if (gemini.preferGemini && gemini.apiKey) {
    args.push("--prefer-gemini");
  }
  if (gemini.apiKey) {
    args.push("--gemini-key", gemini.apiKey);
  }
  if (gemini.model) {
    args.push("--gemini-model", gemini.model);
  }
  if (gemini.timeout) {
    args.push("--gemini-timeout", String(gemini.timeout));
  }
  if (!gemini.enabled) {
    args.push("--gemini-enabled", "false");
  }

  try {
    return await runAdapter<DiagnoseResult>(args, rootDir, python);
  } catch (err) {
    return { status: "error", message: (err as Error).message };
  }
}

async function fetchCodeReview(
  filePath: string,
  sourceCode: string,
  selectionRange: { start_line: number; end_line: number } | null,
  context: vscode.ExtensionContext
): Promise<GeminiReviewResult> {
  const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
  const backendUrl = cfg.get<string>("backendUrl")?.trim() ?? "";
  const gemini = getGeminiSettings();

  if (backendUrl) {
    try {
      return await httpRequest<GeminiReviewResult>(`${backendUrl}/check-code`, {
        source_code: sourceCode,
        selection_range: selectionRange,
        file_path: filePath,
        gemini_api_key: gemini.apiKey || undefined,
        gemini_model: gemini.model,
        gemini_enabled: gemini.enabled,
        gemini_timeout: gemini.timeout,
      });
    } catch (err) {
      getChannel().appendLine(
        `Backend HTTP check-code failed (${(err as Error).message}), falling back to Python CLI…`
      );
    }
  }

  const python = cfg.get<string>("pythonPath") ?? "python";
  const rootDir = repoRoot(context);
  const args = [
    path.join(rootDir, "backend", "diagnose.py"),
    "--file",
    filePath,
    "--check-code",
  ];

  if (selectionRange) {
    args.push("--selection-start", String(selectionRange.start_line));
    args.push("--selection-end", String(selectionRange.end_line));
  }
  if (gemini.apiKey) {
    args.push("--gemini-key", gemini.apiKey);
  }
  if (gemini.model) {
    args.push("--gemini-model", gemini.model);
  }
  if (gemini.timeout) {
    args.push("--gemini-timeout", String(gemini.timeout));
  }
  if (!gemini.enabled) {
    args.push("--gemini-enabled", "false");
  }

  try {
    return await runAdapter<GeminiReviewResult>(args, rootDir, python);
  } catch (err) {
    return {
      status: "error",
      title: "Gemini AI Analysis",
      file: path.basename(filePath),
      message: (err as Error).message,
      findings: [],
    };
  }
}

// ---------------------------------------------------------------------------
// Accurate Location Resolution for VS Code Markers & Highlights
// ---------------------------------------------------------------------------

function computeDiagnosticRange(
  document: vscode.TextDocument,
  d: DiagnosisWithRaw
): { range: vscode.Range; isPrecise: boolean } {
  const rc = d.root_cause_location;
  const raw = d.raw;

  // 1. Prefer resolved root-cause location if line is valid
  if (rc && rc.line != null && rc.line >= 1) {
    const lineIdx = Math.min(rc.line - 1, document.lineCount - 1);
    const lineText = document.lineAt(lineIdx).text;

    let startCol = 0;
    let endCol = lineText.length;

    if (rc.column != null && rc.column >= 1) {
      startCol = Math.min(rc.column - 1, lineText.length);
      if (rc.end_column != null && rc.end_column > rc.column) {
        endCol = Math.min(rc.end_column - 1, lineText.length);
      } else {
        // Highlight at least 1 character or token
        endCol = Math.min(startCol + 1, lineText.length);
      }
      return {
        range: new vscode.Range(lineIdx, startCol, lineIdx, Math.max(startCol + 1, endCol)),
        isPrecise: true,
      };
    }

    // If column not known, highlight statement or non-whitespace range
    const firstNonWs = lineText.search(/\S/);
    if (firstNonWs !== -1) {
      startCol = firstNonWs;
      endCol = lineText.trimEnd().length;
    }
    return {
      range: new vscode.Range(lineIdx, startCol, lineIdx, endCol),
      isPrecise: false,
    };
  }

  // 2. Fallback to compiler location reported by GCC
  if (raw && raw.line != null && raw.line >= 1) {
    const lineIdx = Math.min(raw.line - 1, document.lineCount - 1);
    const lineText = document.lineAt(lineIdx).text;
    const startCol = raw.column != null ? Math.min(raw.column - 1, lineText.length) : 0;
    const endCol = Math.min(startCol + 1, lineText.length);
    return {
      range: new vscode.Range(lineIdx, startCol, lineIdx, Math.max(startCol + 1, endCol)),
      isPrecise: true,
    };
  }

  // 3. Fallback to evidence line
  if (d.evidence && d.evidence.line != null && d.evidence.line >= 1) {
    const lineIdx = Math.min(d.evidence.line - 1, document.lineCount - 1);
    const lineText = document.lineAt(lineIdx).text;
    return {
      range: new vscode.Range(lineIdx, 0, lineIdx, lineText.length),
      isPrecise: false,
    };
  }

  return {
    range: new vscode.Range(0, 0, 0, 1),
    isPrecise: false,
  };
}

function applyVscodeDiagnostics(
  document: vscode.TextDocument,
  diagnoses: DiagnosisWithRaw[]
): void {
  const collection = getDiagnosticCollection();
  const vsdiags: vscode.Diagnostic[] = [];
  const uri = document.uri;

  for (const d of diagnoses) {
    const { range } = computeDiagnosticRange(document, d);

    let modeTag = "[rule-based]";
    if (d.analysis_mode === "gemini") {
      modeTag = "[Gemini AI]";
    } else if (d.analysis_mode === "ai") {
      modeTag = "[AI fallback]";
    }

    const explanation = d.explanation || d.source_explanation || d.compiler_explanation;
    const msg = `[${d.error_type}] ${explanation} ${modeTag}`;

    const diag = new vscode.Diagnostic(range, msg, vscode.DiagnosticSeverity.Error);
    diag.source = "C++ Diagnostic";

    const related: vscode.DiagnosticRelatedInformation[] = [];

    // Show compiler-reported location if distinct from root cause
    if (
      d.compiler_location &&
      d.compiler_location.line != null &&
      d.root_cause_location &&
      d.root_cause_location.line != null &&
      d.compiler_location.line !== d.root_cause_location.line
    ) {
      const compLineIdx = Math.max(0, d.compiler_location.line - 1);
      const compCol = Math.max(0, (d.compiler_location.column ?? 1) - 1);
      const compRange = new vscode.Range(compLineIdx, compCol, compLineIdx, compCol + 1);
      related.push(
        new vscode.DiagnosticRelatedInformation(
          new vscode.Location(uri, compRange),
          `GCC flagged line ${d.compiler_location.line} (message: "${d.compiler_message}")`
        )
      );
    }

    if (d.suggestion) {
      related.push(
        new vscode.DiagnosticRelatedInformation(
          new vscode.Location(uri, range),
          `Suggestion: ${d.suggestion}`
        )
      );
    }

    diag.relatedInformation = related;
    vsdiags.push(diag);
  }

  collection.set(uri, vsdiags);
}

function applyDecorations(
  editor: vscode.TextEditor,
  diagnoses: DiagnosisWithRaw[]
): void {
  const ranges: vscode.DecorationOptions[] = [];
  for (const d of diagnoses) {
    const { range } = computeDiagnosticRange(editor.document, d);
    const hoverText = new vscode.MarkdownString();
    hoverText.appendMarkdown(`**${d.error_type}**: ${d.suggestion || d.compiler_explanation}`);
    ranges.push({
      range,
      hoverMessage: hoverText,
    });
  }
  editor.setDecorations(getDecorationType(), ranges);
}

// ---------------------------------------------------------------------------
// Output channel rendering
// ---------------------------------------------------------------------------

function renderClean(filePath: string): string {
  const name = path.basename(filePath);
  return [
    "─".repeat(60),
    `  ✓  ${name}  compiled cleanly — no errors found.`,
    "─".repeat(60),
  ].join("\n");
}

function renderOneDiagnosis(
  d: DiagnosisWithRaw,
  index: number,
  total: number
): string {
  let modeTag = "[rule-based]";
  if (d.analysis_mode === "gemini") {
    modeTag = "[Gemini AI]";
  } else if (d.analysis_mode === "ai") {
    modeTag = "[AI fallback]";
  }

  const rc = d.root_cause_location;
  const comp = d.compiler_location;
  const rcLocStr =
    rc && rc.line != null
      ? `line ${rc.line}${rc.column != null ? `:${rc.column}` : ""}`
      : "unknown";
  const compLocStr =
    comp && comp.line != null
      ? `line ${comp.line}${comp.column != null ? `:${comp.column}` : ""}`
      : "unknown";

  const header = total > 1 ? `  ERROR ${index + 1} of ${total}` : "  C++ DIAGNOSTIC";
  const lines: string[] = [
    "─".repeat(60),
    header,
    "─".repeat(60),
    `  Error type          : ${d.error_type}  ${modeTag}`,
    `  Root cause location : ${rcLocStr}`,
    `  Compiler reported   : ${compLocStr}`,
    "",
    "  COMPILER MESSAGE",
    `    ${d.compiler_message}`,
    "",
    "  WHAT THE COMPILER MEANS",
    `    ${d.compiler_explanation}`,
    "",
    "  SOURCE EXPLANATION",
    `    ${d.source_explanation}`,
  ];

  if (d.evidence && d.evidence.code) {
    lines.push(
      "",
      "  EVIDENCE",
      `    Line ${d.evidence.line}:  ${d.evidence.code.trim()}`
    );
  }

  lines.push(
    "",
    "  WHAT TO CHECK",
    `    ${d.what_to_check}`,
    "",
    "  SUGGESTION",
    `    ${d.suggestion}`
  );

  return lines.join("\n");
}

function renderAllDiagnoses(
  diagnoses: DiagnosisWithRaw[],
  filePath: string
): string {
  const name = path.basename(filePath);
  const count = diagnoses.length;
  const header = [
    "═".repeat(60),
    `  C++ DIAGNOSTIC  —  ${name}  (${count} error${count !== 1 ? "s" : ""})`,
    "═".repeat(60),
  ].join("\n");
  const bodies = diagnoses.map((d, i) => renderOneDiagnosis(d, i, count));
  return [header, ...bodies, "─".repeat(60)].join("\n");
}

function renderGeminiReview(review: GeminiReviewResult): string {
  const lines: string[] = [
    "═".repeat(60),
    `  GEMINI AI CODE ANALYSIS  —  ${review.file || "Code Review"}`,
    "═".repeat(60),
    `  Scope: ${review.scope || "C++ Source"}`,
    "",
  ];

  if (review.status === "disabled") {
    lines.push(`  STATUS: DISABLED\n  ${review.message}`);
    return lines.join("\n");
  }

  if (review.status === "error") {
    lines.push(`  STATUS: ERROR\n  ${review.message}`);
    return lines.join("\n");
  }

  lines.push(`  SUMMARY:\n    ${review.summary}\n`);

  if (!review.findings || review.findings.length === 0) {
    lines.push("  ✓ No bugs, runtime risks, or suspicious patterns identified.");
  } else {
    lines.push(`  FINDINGS (${review.findings.length}):`);
    review.findings.forEach((f, idx) => {
      const loc = f.line != null ? ` [Line ${f.line}]` : "";
      lines.push(
        "─".repeat(50),
        `  #${idx + 1} [${(f.category || "issue").toUpperCase()}] ${f.title}${loc}`,
        `    Explanation: ${f.explanation}`,
        `    Suggested Fix: ${f.suggestion}`
      );
    });
  }

  lines.push("─".repeat(60));
  return lines.join("\n");
}

function renderError(message: string): string {
  return [
    "─".repeat(60),
    "  C++ DIAGNOSTIC  —  ERROR",
    "─".repeat(60),
    `  ${message}`,
    "─".repeat(60),
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Webview panel — rich diagnostic & Gemini review presentation
// ---------------------------------------------------------------------------

function escapeHtml(s: string): string {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildWebviewContent(
  diagnoses: DiagnosisWithRaw[] | null,
  review: GeminiReviewResult | null,
  filePath: string
): string {
  const name = path.basename(filePath);

  let bodyContent = "";

  if (diagnoses && diagnoses.length > 0) {
    const count = diagnoses.length;
    const errorCards = diagnoses
      .map((d, i) => {
        let modeLabel = "Deterministic Rule";
        let modeBadgeColor = "#1a7f37";
        if (d.analysis_mode === "gemini") {
          modeLabel = "Gemini AI";
          modeBadgeColor = "#2563eb";
        } else if (d.analysis_mode === "ai") {
          modeLabel = "AI Fallback";
          modeBadgeColor = "#7c5cd8";
        }

        const rc = d.root_cause_location;
        const comp = d.compiler_location;
        const rcText =
          rc && rc.line != null
            ? `Root Cause: Line ${rc.line}${rc.column != null ? `:${rc.column}` : ""}`
            : "Unknown";
        const compText =
          comp && comp.line != null ? `GCC: Line ${comp.line}` : "";

        const evidenceHtml =
          d.evidence && d.evidence.code
            ? `<pre class="code">${escapeHtml(d.evidence.code)}</pre>`
            : `<p class="muted">No source evidence available.</p>`;

        return `
<div class="error-card" id="error-${i + 1}">
  <div class="error-card-header">
    <span class="error-index">${i + 1}</span>
    <span class="error-type-label">${escapeHtml(d.error_type)}</span>
    <span class="badge" style="background:${modeBadgeColor}">${escapeHtml(modeLabel)}</span>
    <span class="error-location">${escapeHtml(rcText)} ${compText ? `<span class="muted">(${escapeHtml(compText)})</span>` : ""}</span>
  </div>

  <div class="section">
    <div class="section-title">Compiler Message</div>
    <div class="section-body"><pre class="code">${escapeHtml(d.compiler_message)}</pre></div>
  </div>
  <div class="section">
    <div class="section-title">Root Cause Analysis</div>
    <div class="section-body">${escapeHtml(d.source_explanation || d.explanation || "")}</div>
  </div>
  <div class="section">
    <div class="section-title">Compiler Explanation</div>
    <div class="section-body">${escapeHtml(d.compiler_explanation)}</div>
  </div>
  <div class="section">
    <div class="section-title">Source Evidence</div>
    <div class="section-body">${evidenceHtml}</div>
  </div>
  <div class="section">
    <div class="section-title">What to Check</div>
    <div class="section-body">${escapeHtml(d.what_to_check)}</div>
  </div>
  <div class="section">
    <div class="section-title">Recommended Fix</div>
    <div class="suggestion">${escapeHtml(d.suggestion)}</div>
  </div>
</div>`;
      })
      .join("\n");

    bodyContent = `
<div class="header">
  <h1>C++ Diagnostic</h1>
  <span class="error-count">${count} error${count !== 1 ? "s" : ""}</span>
  <span class="subtitle">${escapeHtml(name)}</span>
</div>
<div class="content">${errorCards}</div>`;
  } else if (review) {
    let findingsHtml = "";
    if (review.status === "disabled") {
      findingsHtml = `<div class="warning-box"><h3>Gemini AI Not Configured</h3><p>${escapeHtml(review.message || "")}</p></div>`;
    } else if (review.status === "error") {
      findingsHtml = `<div class="error-box"><h3>Gemini AI Analysis Error</h3><p>${escapeHtml(review.message || "")}</p></div>`;
    } else if (!review.findings || review.findings.length === 0) {
      findingsHtml = `<div class="clean-box"><h3>✓ Clean Code Review</h3><p>Gemini identified no bugs or critical runtime risks.</p></div>`;
    } else {
      findingsHtml = review.findings
        .map((f, idx) => {
          const loc = f.line != null ? `Line ${f.line}` : "";
          return `
<div class="finding-card">
  <div class="finding-header">
    <span class="finding-idx">#${idx + 1}</span>
    <span class="finding-title">${escapeHtml(f.title)}</span>
    <span class="category-badge cat-${escapeHtml(f.category || "issue")}">${escapeHtml(f.category || "finding")}</span>
    ${loc ? `<span class="finding-loc">${escapeHtml(loc)}</span>` : ""}
  </div>
  <div class="finding-body">
    <p><strong>Analysis:</strong> ${escapeHtml(f.explanation)}</p>
    <div class="suggestion"><strong>Fix / Recommendation:</strong> ${escapeHtml(f.suggestion)}</div>
  </div>
</div>`;
        })
        .join("\n");
    }

    bodyContent = `
<div class="header" style="background:#1e3a8a">
  <h1>Gemini AI Code Analysis</h1>
  <span class="badge" style="background:#2563eb">AI Review</span>
  <span class="subtitle">${escapeHtml(name)} — ${escapeHtml(review.scope || "")}</span>
</div>
<div class="content">
  ${review.summary ? `<div class="summary-box"><strong>Summary:</strong> ${escapeHtml(review.summary)}</div>` : ""}
  ${findingsHtml}
</div>`;
  }

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>C++ Diagnostic & Gemini</title>
<style>
  body { font-family: -apple-system, 'Segoe UI', system-ui, sans-serif; font-size: 14px; line-height: 1.6; margin: 0; padding: 0; background: var(--vscode-editor-background); color: var(--vscode-editor-foreground); }
  .header { color: #fff; padding: 12px 20px; border-bottom: 1px solid var(--vscode-panel-border); display: flex; align-items: baseline; gap: 10px; background: var(--vscode-titleBar-activeBackground); }
  .header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  .header .subtitle { font-size: 12px; opacity: 0.85; }
  .error-count { font-size: 12px; background: var(--vscode-editorError-foreground, #e84); color: #fff; padding: 1px 7px; border-radius: 10px; font-weight: 600; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; color: #fff; }
  .content { padding: 16px 20px; max-width: 820px; }
  .error-card, .finding-card { margin-bottom: 22px; border: 1px solid var(--vscode-panel-border); border-radius: 6px; overflow: hidden; background: var(--vscode-editor-background); }
  .error-card-header, .finding-header { display: flex; align-items: center; gap: 8px; padding: 10px 14px; background: var(--vscode-sideBar-background, rgba(0,0,0,0.06)); border-bottom: 1px solid var(--vscode-panel-border); flex-wrap: wrap; }
  .error-index, .finding-idx { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; background: #e11d48; color: #fff; font-size: 11px; font-weight: 700; flex-shrink: 0; }
  .error-type-label, .finding-title { font-size: 14px; font-weight: 700; color: var(--vscode-editorError-foreground, #e44); }
  .error-location, .finding-loc { font-size: 12px; color: var(--vscode-descriptionForeground); margin-left: auto; }
  .section, .finding-body { padding: 10px 14px; border-bottom: 1px solid var(--vscode-panel-border); }
  .section:last-child { border-bottom: none; }
  .section-title { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--vscode-descriptionForeground); margin-bottom: 5px; }
  .section-body { padding: 8px 12px; background: var(--vscode-textCodeBlock-background, rgba(0,0,0,0.08)); border-radius: 4px; border-left: 3px solid var(--vscode-panel-border); }
  pre.code { margin: 0; font-family: var(--vscode-editor-font-family, 'Courier New', monospace); font-size: 12px; white-space: pre-wrap; word-break: break-all; }
  .muted { color: var(--vscode-descriptionForeground); opacity: 0.8; }
  .suggestion { padding: 8px 12px; background: var(--vscode-diffEditor-insertedLineBackground, rgba(40,167,69,0.1)); border-left: 3px solid #1a7f37; border-radius: 0 4px 4px 0; margin-top: 6px; }
  .summary-box { background: rgba(59,130,246,0.1); border: 1px solid rgba(59,130,246,0.3); padding: 12px 16px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; }
  .warning-box { background: rgba(245,158,11,0.1); border: 1px solid rgba(245,158,11,0.3); padding: 16px; border-radius: 6px; }
  .error-box { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); padding: 16px; border-radius: 6px; }
  .clean-box { background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.3); padding: 16px; border-radius: 6px; }
  .category-badge { text-transform: uppercase; font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 700; background: #475569; color: #fff; }
  .cat-bug { background: #dc2626; }
  .cat-logic { background: #d97706; }
  .cat-runtime_risk { background: #b91c1c; }
  .cat-maintainability { background: #4f46e5; }
  .cat-improvement { background: #059669; }
</style>
</head>
<body>
${bodyContent}
</body>
</html>`;
}

function showOrUpdateWebviewPanel(
  diagnoses: DiagnosisWithRaw[] | null,
  review: GeminiReviewResult | null,
  filePath: string,
  context: vscode.ExtensionContext
): void {
  if (!webviewPanel) {
    webviewPanel = vscode.window.createWebviewPanel(
      "cppDiagnosticPanel",
      "C++ Diagnostic & AI",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: false, retainContextWhenHidden: true }
    );
    webviewPanel.onDidDispose(
      () => {
        webviewPanel = undefined;
      },
      null,
      context.subscriptions
    );
  }
  webviewPanel.webview.html = buildWebviewContent(diagnoses, review, filePath);
  webviewPanel.reveal(vscode.ViewColumn.Beside, true);
}

// ---------------------------------------------------------------------------
// Hover provider — shows full explanation on root cause or compiler line
// ---------------------------------------------------------------------------

function buildHoverProvider(): vscode.HoverProvider {
  return {
    provideHover(
      document: vscode.TextDocument,
      position: vscode.Position
    ): vscode.Hover | undefined {
      const diagnoses = lastDiagnostics.get(document.uri.toString());
      if (!diagnoses || diagnoses.length === 0) {
        return undefined;
      }

      // Match hovered line to root-cause line or compiler line
      const hovered = diagnoses.find((d) => {
        const rcLine = d.root_cause_location?.line;
        if (rcLine != null && rcLine - 1 === position.line) {
          return true;
        }
        const compLine = d.compiler_location?.line ?? d.raw?.line;
        if (compLine != null && compLine - 1 === position.line) {
          return true;
        }
        return false;
      });

      if (!hovered) {
        return undefined;
      }

      const d = hovered;
      let modeTag = "deterministic rule";
      if (d.analysis_mode === "gemini") {
        modeTag = "Gemini AI";
      } else if (d.analysis_mode === "ai") {
        modeTag = "AI fallback";
      }

      const md = new vscode.MarkdownString(undefined, true);
      md.isTrusted = false;

      md.appendMarkdown(
        `### C++ Diagnostic — \`${d.error_type}\` *(${modeTag})*\n\n`
      );

      if (
        d.root_cause_location?.line != null &&
        d.compiler_location?.line != null &&
        d.root_cause_location.line !== d.compiler_location.line
      ) {
        md.appendMarkdown(
          `> 🎯 **Root Cause**: Line ${d.root_cause_location.line} *(Compiler flagged line ${d.compiler_location.line})*\n\n`
        );
      }

      md.appendMarkdown(`**Compiler Message**\n\n`);
      md.appendCodeblock(d.compiler_message, "text");

      md.appendMarkdown(
        `**Root Cause Explanation**\n\n${d.source_explanation || d.explanation || d.compiler_explanation}\n\n`
      );

      if (d.evidence && d.evidence.code) {
        md.appendMarkdown(
          `**Evidence** *(Line ${d.evidence.line})*\n\n`
        );
        md.appendCodeblock(d.evidence.code.trim(), "cpp");
      }

      md.appendMarkdown(`**What to check**\n\n${d.what_to_check}\n\n`);
      md.appendMarkdown(`**Suggestion**\n\n${d.suggestion}`);

      const { range } = computeDiagnosticRange(document, d);
      return new vscode.Hover(md, range);
    },
  };
}

// ---------------------------------------------------------------------------
// Core command: runDiagnose
// ---------------------------------------------------------------------------

async function runDiagnose(
  context: vscode.ExtensionContext,
  showPanel: boolean = false
): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("C++ Diagnostic: no active editor.");
    return;
  }

  const doc = editor.document;
  if (!isCppFile(doc.fileName)) {
    vscode.window.showWarningMessage(
      "C++ Diagnostic: active file is not a C++ source file (.cpp / .cc / .cxx)."
    );
    return;
  }

  if (doc.isDirty) {
    await doc.save();
  }

  const channel = getChannel();
  channel.show(true);
  channel.appendLine(`\nDiagnosing ${path.basename(doc.fileName)} …`);

  const result = await fetchDiagnosis(doc.fileName, doc.getText(), context);
  const uri = doc.uri;

  channel.clear();

  switch (result.status) {
    case "clean":
      clearAllMarkers(uri);
      channel.appendLine(renderClean(doc.fileName));
      vscode.window.showInformationMessage(
        `C++ Diagnostic: ${path.basename(doc.fileName)} compiled cleanly.`
      );
      break;

    case "ok": {
      const diagnoses = result.diagnostics ?? [result.diagnosis];
      lastDiagnostics.set(uri.toString(), diagnoses);

      applyVscodeDiagnostics(doc, diagnoses);
      applyDecorations(editor, diagnoses);

      channel.appendLine(renderAllDiagnoses(diagnoses, doc.fileName));

      const count = diagnoses.length;
      if (count > 1) {
        vscode.window.showWarningMessage(
          `C++ Diagnostic: ${count} errors identified in ${path.basename(doc.fileName)}.`
        );
      }

      if (showPanel) {
        showOrUpdateWebviewPanel(diagnoses, null, doc.fileName, context);
      }
      break;
    }

    case "error":
      clearAllMarkers(uri);
      channel.appendLine(renderError(result.message));
      vscode.window.showErrorMessage(`C++ Diagnostic: ${result.message}`);
      break;
  }
}

// ---------------------------------------------------------------------------
// API Key management commands
// ---------------------------------------------------------------------------

async function runSetGeminiApiKey(): Promise<string | undefined> {
  const existingKey =
    vscode.workspace.getConfiguration("cppDiagnostic").get<string>("geminiApiKey") || "";

  const key = await vscode.window.showInputBox({
    prompt: "Enter your Google Gemini API Key for AI-driven compiler diagnostics and code reviews",
    placeHolder: "AIzaSy...",
    value: existingKey,
    password: true,
    ignoreFocusOut: true,
    validateInput: (val) => {
      if (!val || !val.trim()) {
        return "Gemini API key cannot be empty.";
      }
      return null;
    },
  });

  if (key) {
    const trimmed = key.trim();
    const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
    await cfg.update("geminiApiKey", trimmed, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(
      "C++ Diagnostic: Gemini API Key saved! The extension will now use Gemini AI for all diagnostic answers."
    );
    return trimmed;
  }
  return undefined;
}

async function runClearGeminiApiKey(): Promise<void> {
  const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
  await cfg.update("geminiApiKey", "", vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage(
    "C++ Diagnostic: Gemini API Key cleared. The extension will use normal offline fallthrough rules."
  );
}

// ---------------------------------------------------------------------------
// Command: checkCodeWithGemini
// ---------------------------------------------------------------------------

async function runCheckCodeWithGemini(
  context: vscode.ExtensionContext
): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("C++ Diagnostic: no active editor.");
    return;
  }

  const doc = editor.document;
  if (!isCppFile(doc.fileName)) {
    vscode.window.showWarningMessage(
      "C++ Diagnostic: active file is not a C++ source file (.cpp / .cc / .cxx)."
    );
    return;
  }

  const gemini = getGeminiSettings();
  if (!gemini.apiKey) {
    const action = await vscode.window.showWarningMessage(
      "A Gemini API Key is required for Gemini AI Code Analysis. Would you like to set your API Key now?",
      "Set Gemini API Key",
      "Cancel"
    );
    if (action === "Set Gemini API Key") {
      const savedKey = await runSetGeminiApiKey();
      if (!savedKey) {
        return;
      }
    } else {
      return;
    }
  }

  const channel = getChannel();
  channel.show(true);

  let selectionRange: { start_line: number; end_line: number } | null = null;
  if (editor.selection && !editor.selection.isEmpty) {
    selectionRange = {
      start_line: editor.selection.start.line + 1,
      end_line: editor.selection.end.line + 1,
    };
    channel.appendLine(
      `Analyzing selection (lines ${selectionRange.start_line}-${selectionRange.end_line}) with Gemini AI…`
    );
  } else {
    channel.appendLine(`Analyzing ${path.basename(doc.fileName)} with Gemini AI…`);
  }

  const review = await fetchCodeReview(
    doc.fileName,
    doc.getText(),
    selectionRange,
    context
  );

  lastReviewResult = review;
  channel.clear();
  channel.appendLine(renderGeminiReview(review));

  if (review.status === "disabled") {
    vscode.window.showWarningMessage(
      "Gemini AI: Not configured. Set GEMINI_API_KEY environment variable or 'cppDiagnostic.geminiApiKey' setting."
    );
  } else if (review.status === "error") {
    vscode.window.showErrorMessage(`Gemini AI: ${review.message}`);
  } else {
    const findingCount = review.findings?.length ?? 0;
    if (findingCount === 0) {
      vscode.window.showInformationMessage(
        `Gemini AI: Code review completed cleanly — no issues identified.`
      );
    } else {
      vscode.window.showInformationMessage(
        `Gemini AI: Review complete — ${findingCount} finding${findingCount !== 1 ? "s" : ""} identified.`
      );
    }
  }

  showOrUpdateWebviewPanel(null, review, doc.fileName, context);
}

// ---------------------------------------------------------------------------
// Command: analyzeFile — always opens webview panel
// ---------------------------------------------------------------------------

async function runAnalyzeFile(context: vscode.ExtensionContext): Promise<void> {
  return runDiagnose(context, true);
}

// ---------------------------------------------------------------------------
// Command: clearDiagnostics
// ---------------------------------------------------------------------------

function runClearDiagnostics(): void {
  const editor = vscode.window.activeTextEditor;
  if (editor) {
    clearAllMarkers(editor.document.uri);
    vscode.window.showInformationMessage("C++ Diagnostic: cleared.");
  } else {
    getDiagnosticCollection().clear();
    lastDiagnostics.clear();
    vscode.window.showInformationMessage("C++ Diagnostic: all diagnostics cleared.");
  }
}

// ---------------------------------------------------------------------------
// Command: showPanel — show last diagnoses or review in webview
// ---------------------------------------------------------------------------

function runShowPanel(context: vscode.ExtensionContext): void {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("C++ Diagnostic: no active editor.");
    return;
  }
  const diagnoses = lastDiagnostics.get(editor.document.uri.toString());
  if ((!diagnoses || diagnoses.length === 0) && !lastReviewResult) {
    vscode.window.showInformationMessage(
      "C++ Diagnostic: no diagnosis or review available. Run 'Explain Error' or 'Check Code with Gemini' first."
    );
    return;
  }
  showOrUpdateWebviewPanel(
    diagnoses ?? null,
    lastReviewResult ?? null,
    editor.document.fileName,
    context
  );
}

// ---------------------------------------------------------------------------
// Extension lifecycle
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("cppDiagnostic.diagnose", () =>
      runDiagnose(context, false)
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("cppDiagnostic.analyzeFile", () =>
      runAnalyzeFile(context)
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("cppDiagnostic.checkCode", () =>
      runCheckCodeWithGemini(context)
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("cppDiagnostic.setGeminiApiKey", () =>
      runSetGeminiApiKey()
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("cppDiagnostic.clearGeminiApiKey", () =>
      runClearGeminiApiKey()
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("cppDiagnostic.clearDiagnostics", () =>
      runClearDiagnostics()
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("cppDiagnostic.showPanel", () =>
      runShowPanel(context)
    )
  );

  // Hover provider for C++ files
  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      [
        { language: "cpp" },
        { pattern: "**/*.cpp" },
        { pattern: "**/*.cc" },
        { pattern: "**/*.cxx" },
        { pattern: "**/*.h" },
        { pattern: "**/*.hpp" },
      ],
      buildHoverProvider()
    )
  );

  // Auto-analyze on save (if enabled)
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
      if (!cfg.get<boolean>("autoAnalyzeOnSave")) {
        return;
      }
      if (!isCppFile(doc.fileName)) {
        return;
      }

      if (saveDebounceTimer) {
        clearTimeout(saveDebounceTimer);
      }
      saveDebounceTimer = setTimeout(() => {
        const activeEditor = vscode.window.activeTextEditor;
        if (
          activeEditor &&
          activeEditor.document.uri.toString() === doc.uri.toString()
        ) {
          runDiagnose(context, false);
        }
      }, 500);
    })
  );

  // Clear markers when document closes
  context.subscriptions.push(
    vscode.workspace.onDidCloseTextDocument((doc) => {
      clearAllMarkers(doc.uri);
    })
  );

  // Clear stale markers when document changes
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((event) => {
      const uriKey = event.document.uri.toString();
      if (!lastDiagnostics.has(uriKey)) {
        return;
      }
      clearAllMarkers(event.document.uri);
    })
  );
}

export function deactivate(): void {
  if (saveDebounceTimer) {
    clearTimeout(saveDebounceTimer);
  }
  outputChannel?.dispose();
  diagnosticCollection?.dispose();
  decorationType?.dispose();
  webviewPanel?.dispose();
  lastDiagnostics.clear();
  lastReviewResult = undefined;
}
