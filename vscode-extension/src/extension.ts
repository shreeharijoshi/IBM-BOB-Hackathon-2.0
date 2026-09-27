/**
 * C++ Diagnostic — VS Code extension entry point.
 *
 * Provides four commands:
 *   cppDiagnostic.diagnose         — Explain Error (Ctrl+Shift+D)
 *   cppDiagnostic.analyzeFile      — Analyze File (full multi-error panel)
 *   cppDiagnostic.clearDiagnostics — Clear all diagnostics and decorations
 *   cppDiagnostic.showPanel        — Show last explanation in a webview panel
 *
 * Transport:
 *   1. HTTP to a running backend server (cppDiagnostic.backendUrl setting).
 *   2. Python CLI subprocess fallback (cppDiagnostic.pythonPath setting).
 *
 * Presentation layers:
 *   a. DiagnosticCollection   — Problems panel + squiggly underlines (one per error)
 *   b. TextEditorDecoration   — Amber highlights on every error's evidence line
 *   c. HoverProvider          — Full explanation on hover over any flagged line
 *   d. WebviewPanel           — Rich HTML panel showing all errors in a list
 *   e. OutputChannel          — Plain-text log showing all errors in sequence
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

// Debounce timer for auto-analyze on save
let saveDebounceTimer: NodeJS.Timeout | undefined;

// ---------------------------------------------------------------------------
// Types mirroring backend output schema
// ---------------------------------------------------------------------------

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
  analysis_mode: string;
  compiler_message: string;
  compiler_explanation: string;
  source_explanation: string;
  evidence: Evidence;
  what_to_check: string;
  suggestion: string;
}

/** Diagnosis with the original compiler diagnostic attached */
interface DiagnosisWithRaw extends Diagnosis {
  raw: RawError;
}

interface CleanResult { status: "clean"; message?: string }
interface OkResult {
  status: "ok";
  diagnosis: DiagnosisWithRaw;       // first error — backward compat
  diagnostics: DiagnosisWithRaw[];   // all errors — full multi-error
}
interface ErrorResult { status: "error"; message: string }
type DiagnoseResult = CleanResult | OkResult | ErrorResult;

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
      isWholeLine: true,
      overviewRulerColor: new vscode.ThemeColor("editorWarning.foreground"),
      overviewRulerLane: vscode.OverviewRulerLane.Right,
    });
  }
  return decorationType;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Return the directory that contains the `backend/` folder.
 *
 * When the extension is installed from a VSIX the Python backend is bundled
 * directly inside the extension directory, so `extensionPath/backend/diagnose.py`
 * exists and we return `extensionPath`.
 *
 * During development (running from the repository) the backend lives one level
 * up (`<repo-root>/backend/`), so we return `extensionPath/..` as before.
 */
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
  return [".cpp", ".cc", ".cxx", ".c++"].includes(ext);
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

// ---------------------------------------------------------------------------
// Transport: HTTP backend
// ---------------------------------------------------------------------------

function httpRequest(url: string, body: object): Promise<DiagnoseResult> {
  return new Promise((resolve) => {
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
      res.on("data", (chunk) => { raw += chunk; });
      res.on("end", () => {
        try {
          resolve(JSON.parse(raw) as DiagnoseResult);
        } catch {
          resolve({ status: "error", message: `Backend returned non-JSON: ${raw.slice(0, 200)}` });
        }
      });
    });

    req.on("error", (err) => {
      resolve({ status: "error", message: `Backend connection failed: ${err.message}` });
    });

    req.setTimeout(15000, () => {
      req.destroy();
      resolve({ status: "error", message: "Backend request timed out after 15 s." });
    });

    req.write(data);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Transport: Python CLI adapter
// ---------------------------------------------------------------------------

function runAdapter(
  filePath: string,
  rootDir: string,
  python: string
): Promise<DiagnoseResult> {
  return new Promise((resolve) => {
    const args = [
      path.join(rootDir, "backend", "diagnose.py"),
      "--file",
      filePath,
    ];

    let stdout = "";
    let stderr = "";

    let proc: cp.ChildProcess;
    try {
      proc = cp.spawn(python, args, { cwd: rootDir });
    } catch (err) {
      resolve({ status: "error", message: `Failed to start Python: ${(err as Error).message}` });
      return;
    }

    proc.stdout?.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr?.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

    proc.on("error", (err: NodeJS.ErrnoException) => {
      if (err.code === "ENOENT") {
        resolve({
          status: "error",
          message: `Python interpreter not found: "${python}". Set cppDiagnostic.pythonPath in VS Code settings.`,
        });
      } else {
        resolve({ status: "error", message: `Failed to start adapter: ${err.message}` });
      }
    });

    const timer = setTimeout(() => {
      proc.kill();
      resolve({ status: "error", message: "Diagnostic adapter timed out after 30 s." });
    }, 30000);

    proc.on("close", () => {
      clearTimeout(timer);
      const raw = stdout.trim();
      if (!raw) {
        const detail = stderr.trim() || "No output from adapter.";
        resolve({ status: "error", message: `Adapter produced no output.\n    ${detail}` });
        return;
      }
      try {
        resolve(JSON.parse(raw) as DiagnoseResult);
      } catch {
        resolve({
          status: "error",
          message: `Adapter returned non-JSON output:\n    ${raw.slice(0, 200)}`,
        });
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Transport dispatcher
// ---------------------------------------------------------------------------

async function fetchDiagnosis(
  filePath: string,
  sourceCode: string,
  context: vscode.ExtensionContext
): Promise<DiagnoseResult> {
  const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
  const backendUrl = cfg.get<string>("backendUrl")?.trim() ?? "";

  if (backendUrl) {
    return httpRequest(`${backendUrl}/diagnose`, {
      source_code: sourceCode,
      compiler_output: "",
    });
  }

  const python = cfg.get<string>("pythonPath") ?? "python";
  return runAdapter(filePath, repoRoot(context), python);
}

// ---------------------------------------------------------------------------
// VS Code diagnostics — one entry per error, all with full explanations
// ---------------------------------------------------------------------------

function applyVscodeDiagnostics(
  uri: vscode.Uri,
  diagnoses: DiagnosisWithRaw[]
): void {
  const collection = getDiagnosticCollection();
  const vsdiags: vscode.Diagnostic[] = [];
  const seenLines = new Set<number>();

  for (const d of diagnoses) {
    const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
    // Use raw compiler line if evidence line is a duplicate (can happen with
    // cascade errors that share the same source line).
    const line = seenLines.has(evidenceLine) && d.raw.line != null
      ? d.raw.line - 1
      : evidenceLine;
    seenLines.add(line);

    const col = d.raw.column != null ? d.raw.column - 1 : 0;
    const range = new vscode.Range(line, col, line, 9999);
    const modeTag = d.analysis_mode === "deterministic" ? "[rule-based]" : "[AI]";
    const msg = `[${d.error_type}] ${d.compiler_message}  —  ${d.compiler_explanation}  ${modeTag}`;

    const diag = new vscode.Diagnostic(range, msg, vscode.DiagnosticSeverity.Error);
    diag.source = "C++ Diagnostic";
    diag.relatedInformation = [
      new vscode.DiagnosticRelatedInformation(
        new vscode.Location(uri, range),
        `Suggestion: ${d.suggestion}`
      ),
    ];
    vsdiags.push(diag);
  }

  collection.set(uri, vsdiags);
}

function applyDecorations(editor: vscode.TextEditor, diagnoses: DiagnosisWithRaw[]): void {
  const ranges: vscode.DecorationOptions[] = [];
  const seen = new Set<number>();
  for (const d of diagnoses) {
    const line = d.evidence.line != null ? d.evidence.line - 1 : 0;
    if (seen.has(line)) { continue; }
    seen.add(line);
    ranges.push({ range: new vscode.Range(line, 0, line, 9999) });
  }
  editor.setDecorations(getDecorationType(), ranges);
}

// ---------------------------------------------------------------------------
// Output channel rendering
// ---------------------------------------------------------------------------

function renderClean(filePath: string): string {
  const name = path.basename(filePath);
  return ["─".repeat(60), `  ✓  ${name}  compiled cleanly — no errors found.`, "─".repeat(60)].join("\n");
}

function renderOneDiagnosis(d: DiagnosisWithRaw, index: number, total: number): string {
  const modeTag = d.analysis_mode === "deterministic" ? "[rule-based]" : "[AI fallback]";
  const lineTag = d.evidence.line != null ? `line ${d.evidence.line}` : "unknown line";
  const header = total > 1 ? `  ERROR ${index + 1} of ${total}` : "  C++ DIAGNOSTIC";
  const lines: string[] = [
    "─".repeat(60),
    header,
    "─".repeat(60),
    `  Error type   : ${d.error_type}  ${modeTag}`,
    `  Location     : ${lineTag}`,
    "",
    "  COMPILER MESSAGE",
    `    ${d.compiler_message}`,
    "",
    "  WHAT THE COMPILER MEANS",
    `    ${d.compiler_explanation}`,
    "",
    "  SOURCE PATTERN",
    `    ${d.source_explanation}`,
  ];
  if (d.evidence.code) {
    lines.push("", "  EVIDENCE", `    ${lineTag}:  ${d.evidence.code.trim()}`);
  }
  lines.push(
    "",
    "  WHAT TO CHECK",
    `    ${d.what_to_check}`,
    "",
    "  SUGGESTION",
    `    ${d.suggestion}`,
  );
  return lines.join("\n");
}

function renderAllDiagnoses(diagnoses: DiagnosisWithRaw[], filePath: string): string {
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

function renderError(message: string): string {
  return ["─".repeat(60), "  C++ DIAGNOSTIC  —  ERROR", "─".repeat(60), `  ${message}`, "─".repeat(60)].join("\n");
}

// ---------------------------------------------------------------------------
// Webview panel — list of all errors
// ---------------------------------------------------------------------------

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildWebviewContent(diagnoses: DiagnosisWithRaw[], filePath: string): string {
  const name = path.basename(filePath);
  const count = diagnoses.length;

  const errorCards = diagnoses.map((d, i) => {
    const modeLabel = d.analysis_mode === "deterministic" ? "Rule-Based" : "AI Fallback";
    const modeBadgeColor = d.analysis_mode === "deterministic" ? "#1a7f37" : "#7c5cd8";
    const lineTag = d.evidence.line != null ? `Line ${d.evidence.line}` : "Unknown line";
    const evidenceHtml = d.evidence.code
      ? `<pre class="code">${escapeHtml(d.evidence.code)}</pre>`
      : `<p class="muted">No source evidence available.</p>`;

    return `
<div class="error-card" id="error-${i + 1}">
  <div class="error-card-header">
    <span class="error-index">${i + 1}</span>
    <span class="error-type-label">${escapeHtml(d.error_type)}</span>
    <span class="badge" style="background:${modeBadgeColor}">${escapeHtml(modeLabel)}</span>
    <span class="error-location">${escapeHtml(lineTag)}</span>
  </div>

  <div class="section">
    <div class="section-title">Compiler Message</div>
    <div class="section-body"><pre class="code">${escapeHtml(d.compiler_message)}</pre></div>
  </div>
  <div class="section">
    <div class="section-title">What the Compiler Means</div>
    <div class="section-body">${escapeHtml(d.compiler_explanation)}</div>
  </div>
  <div class="section">
    <div class="section-title">Source Pattern</div>
    <div class="section-body">${escapeHtml(d.source_explanation)}</div>
  </div>
  <div class="section">
    <div class="section-title">Evidence (${escapeHtml(lineTag)})</div>
    <div class="section-body">${evidenceHtml}</div>
  </div>
  <div class="section">
    <div class="section-title">What to Check</div>
    <div class="section-body">${escapeHtml(d.what_to_check)}</div>
  </div>
  <div class="section">
    <div class="section-title">Suggestion</div>
    <div class="suggestion">${escapeHtml(d.suggestion)}</div>
  </div>
</div>`;
  }).join("\n");

  // Error summary nav bar (only shown when > 1 error)
  const navHtml = count > 1
    ? `<div class="error-nav">
        ${diagnoses.map((d, i) => {
          const lineTag = d.evidence.line != null ? `L${d.evidence.line}` : "?";
          return `<a class="nav-item" href="#error-${i + 1}">#${i + 1} ${escapeHtml(d.error_type)} <span class="nav-line">${escapeHtml(lineTag)}</span></a>`;
        }).join("")}
       </div>`
    : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>C++ Diagnostic</title>
<style>
  body { font-family: -apple-system, 'Segoe UI', system-ui, sans-serif; font-size: 14px; line-height: 1.6; margin: 0; padding: 0; background: var(--vscode-editor-background); color: var(--vscode-editor-foreground); }
  .header { background: var(--vscode-titleBar-activeBackground); color: var(--vscode-titleBar-activeForeground); padding: 12px 20px; border-bottom: 1px solid var(--vscode-panel-border); display: flex; align-items: baseline; gap: 10px; }
  .header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  .header .subtitle { font-size: 12px; opacity: 0.75; }
  .error-count { font-size: 12px; background: var(--vscode-editorError-foreground, #e84); color: #fff; padding: 1px 7px; border-radius: 10px; font-weight: 600; }
  .error-nav { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 20px; border-bottom: 1px solid var(--vscode-panel-border); background: var(--vscode-sideBar-background, rgba(0,0,0,0.05)); }
  .nav-item { font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 4px; background: var(--vscode-textCodeBlock-background, rgba(0,0,0,0.1)); color: var(--vscode-editor-foreground); text-decoration: none; border: 1px solid var(--vscode-panel-border); white-space: nowrap; }
  .nav-item:hover { background: var(--vscode-list-hoverBackground); }
  .nav-line { opacity: 0.6; font-weight: 400; }
  .content { padding: 16px 20px; max-width: 780px; }
  .error-card { margin-bottom: 24px; border: 1px solid var(--vscode-panel-border); border-radius: 6px; overflow: hidden; }
  .error-card-header { display: flex; align-items: center; gap: 8px; padding: 10px 14px; background: var(--vscode-sideBar-background, rgba(0,0,0,0.06)); border-bottom: 1px solid var(--vscode-panel-border); flex-wrap: wrap; }
  .error-index { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; background: var(--vscode-editorError-foreground, #e44); color: #fff; font-size: 11px; font-weight: 700; flex-shrink: 0; }
  .error-type-label { font-size: 14px; font-weight: 700; color: var(--vscode-editorError-foreground, #e44); }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px; font-weight: 600; color: #fff; }
  .error-location { font-size: 12px; color: var(--vscode-descriptionForeground); margin-left: auto; }
  .section { padding: 10px 14px; border-bottom: 1px solid var(--vscode-panel-border); }
  .section:last-child { border-bottom: none; }
  .section-title { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--vscode-descriptionForeground); margin-bottom: 5px; }
  .section-body { padding: 8px 12px; background: var(--vscode-textCodeBlock-background, rgba(0,0,0,0.08)); border-radius: 4px; border-left: 3px solid var(--vscode-panel-border); }
  pre.code { margin: 0; font-family: var(--vscode-editor-font-family, 'Courier New', monospace); font-size: 12px; white-space: pre-wrap; word-break: break-all; }
  .muted { color: var(--vscode-descriptionForeground); font-style: italic; margin: 0; }
  .suggestion { padding: 8px 12px; background: var(--vscode-diffEditor-insertedLineBackground, rgba(40,167,69,0.1)); border-left: 3px solid #1a7f37; border-radius: 0 4px 4px 0; }
</style>
</head>
<body>
<div class="header">
  <h1>C++ Diagnostic</h1>
  <span class="error-count">${count} error${count !== 1 ? "s" : ""}</span>
  <span class="subtitle">${escapeHtml(name)}</span>
</div>
${navHtml}
<div class="content">
${errorCards}
</div>
</body>
</html>`;
}

function showOrUpdateWebviewPanel(
  diagnoses: DiagnosisWithRaw[],
  filePath: string,
  context: vscode.ExtensionContext
): void {
  if (!webviewPanel) {
    webviewPanel = vscode.window.createWebviewPanel(
      "cppDiagnosticPanel",
      "C++ Diagnostic",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: false, retainContextWhenHidden: true }
    );
    webviewPanel.onDidDispose(() => { webviewPanel = undefined; }, null, context.subscriptions);
  }
  webviewPanel.webview.html = buildWebviewContent(diagnoses, filePath);
  webviewPanel.reveal(vscode.ViewColumn.Beside, true);
}

// ---------------------------------------------------------------------------
// Hover provider — shows full explanation for whichever error line is hovered
// ---------------------------------------------------------------------------

function buildHoverProvider(): vscode.HoverProvider {
  return {
    provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
      const diagnoses = lastDiagnostics.get(document.uri.toString());
      if (!diagnoses || diagnoses.length === 0) { return undefined; }

      // Find the diagnosis whose evidence line matches the hovered line.
      const hovered = diagnoses.find(
        (d) => d.evidence.line != null && d.evidence.line - 1 === position.line
      );
      if (!hovered) { return undefined; }

      const d = hovered;
      const modeTag = d.analysis_mode === "deterministic" ? "rule-based" : "AI fallback";
      const total = diagnoses.length;
      const idx = diagnoses.indexOf(d) + 1;
      const countStr = total > 1 ? ` (${idx}/${total})` : "";

      const md = new vscode.MarkdownString(undefined, true);
      md.isTrusted = false;

      md.appendMarkdown(`### C++ Diagnostic — \`${d.error_type}\`${countStr} *(${modeTag})*\n\n`);
      md.appendMarkdown(`**Compiler message**\n\n`);
      md.appendCodeblock(d.compiler_message, "text");
      md.appendMarkdown(`**What the compiler means**\n\n${d.compiler_explanation}\n\n`);
      md.appendMarkdown(`**Source pattern**\n\n${d.source_explanation}\n\n`);
      if (d.evidence.code) {
        md.appendMarkdown(`**Evidence** *(line ${d.evidence.line})*\n\n`);
        md.appendCodeblock(d.evidence.code.trim(), "cpp");
      }
      md.appendMarkdown(`**What to check**\n\n${d.what_to_check}\n\n`);
      md.appendMarkdown(`**Suggestion**\n\n${d.suggestion}`);

      const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
      const range = new vscode.Range(evidenceLine, 0, evidenceLine, 9999);
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

  if (doc.isDirty) { await doc.save(); }

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
      vscode.window.showInformationMessage(`C++ Diagnostic: ${path.basename(doc.fileName)} compiled cleanly.`);
      break;

    case "ok": {
      const diagnoses = result.diagnostics ?? [result.diagnosis];
      lastDiagnostics.set(uri.toString(), diagnoses);

      applyVscodeDiagnostics(uri, diagnoses);
      applyDecorations(editor, diagnoses);

      channel.appendLine(renderAllDiagnoses(diagnoses, doc.fileName));

      const count = diagnoses.length;
      if (count > 1) {
        vscode.window.showWarningMessage(
          `C++ Diagnostic: ${count} errors found in ${path.basename(doc.fileName)}.`
        );
      }

      if (showPanel) {
        showOrUpdateWebviewPanel(diagnoses, doc.fileName, context);
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
// Command: showPanel — show last diagnoses in webview
// ---------------------------------------------------------------------------

function runShowPanel(context: vscode.ExtensionContext): void {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("C++ Diagnostic: no active editor.");
    return;
  }
  const diagnoses = lastDiagnostics.get(editor.document.uri.toString());
  if (!diagnoses || diagnoses.length === 0) {
    vscode.window.showInformationMessage("C++ Diagnostic: no diagnosis available. Run 'Explain Error' first.");
    return;
  }
  showOrUpdateWebviewPanel(diagnoses, editor.document.fileName, context);
}

// ---------------------------------------------------------------------------
// Extension lifecycle
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "cppDiagnostic.diagnose",
      () => runDiagnose(context, false)
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "cppDiagnostic.analyzeFile",
      () => runAnalyzeFile(context)
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "cppDiagnostic.clearDiagnostics",
      () => runClearDiagnostics()
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "cppDiagnostic.showPanel",
      () => runShowPanel(context)
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
      ],
      buildHoverProvider()
    )
  );

  // Auto-analyze on save (if enabled)
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
      if (!cfg.get<boolean>("autoAnalyzeOnSave")) { return; }
      if (!isCppFile(doc.fileName)) { return; }

      if (saveDebounceTimer) { clearTimeout(saveDebounceTimer); }
      saveDebounceTimer = setTimeout(() => {
        const activeEditor = vscode.window.activeTextEditor;
        if (activeEditor && activeEditor.document.uri.toString() === doc.uri.toString()) {
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
      if (!lastDiagnostics.has(uriKey)) { return; }
      clearAllMarkers(event.document.uri);
    })
  );
}

export function deactivate(): void {
  if (saveDebounceTimer) { clearTimeout(saveDebounceTimer); }
  outputChannel?.dispose();
  diagnosticCollection?.dispose();
  decorationType?.dispose();
  webviewPanel?.dispose();
  lastDiagnostics.clear();
}
