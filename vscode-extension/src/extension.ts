/**
 * C++ Diagnostic — VS Code extension entry point.
 *
 * When the "Diagnose C++" command is invoked the extension:
 *   1. Resolves the active C++ editor and saves if dirty.
 *   2. Spawns `python backend/diagnose.py --file <path>`.
 *   3. Parses the JSON result from the adapter.
 *   4. Applies results through three VS Code presentation layers:
 *        a. DiagnosticCollection  → Problems panel + editor squiggly underline
 *        b. TextEditorDecorationType → subtle background highlight on evidence line
 *        c. HoverProvider         → full plain-language explanation on hover
 *   5. Writes the same information to the Output Channel as a readable fallback.
 *
 * On "clean" or "error" all previous decorations/diagnostics are cleared.
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as path from "path";

// ---------------------------------------------------------------------------
// Module-level state (one set per extension lifetime)
// ---------------------------------------------------------------------------

let outputChannel: vscode.OutputChannel | undefined;
let diagnosticCollection: vscode.DiagnosticCollection | undefined;

/** Decoration type for the evidence-line highlight.  Created once. */
let decorationType: vscode.TextEditorDecorationType | undefined;

/**
 * The last successful diagnosis keyed by file URI string.
 * The hover provider reads from this map.
 */
const lastDiagnosis = new Map<string, OkResult["diagnosis"]>();

// ---------------------------------------------------------------------------
// Types mirroring backend/diagnose.py output schema
// ---------------------------------------------------------------------------

interface CleanResult { status: "clean" }
interface OkResult {
  status: "ok";
  diagnosis: {
    error_type: string;
    analysis_mode: string;
    compiler_message: string;
    compiler_explanation: string;
    source_explanation: string;
    evidence: { line: number | null; code: string };
    what_to_check: string;
    suggestion: string;
  };
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
      // Subtle amber background on the evidence line; visible in both themes.
      backgroundColor: new vscode.ThemeColor("diffEditor.insertedLineBackground"),
      isWholeLine: true,
      overviewRulerColor: new vscode.ThemeColor("editorWarning.foreground"),
      overviewRulerLane: vscode.OverviewRulerLane.Right,
    });
  }
  return decorationType;
}

// ---------------------------------------------------------------------------
// Helpers: repository root
// ---------------------------------------------------------------------------

/** One level above vscode-extension/ */
function repoRoot(context: vscode.ExtensionContext): string {
  return path.resolve(context.extensionPath, "..");
}

// ---------------------------------------------------------------------------
// Helpers: Output Channel rendering (fallback / full-text view)
// ---------------------------------------------------------------------------

function renderClean(filePath: string): string {
  const name = path.basename(filePath);
  return [
    "─".repeat(60),
    `  ✓  ${name}  compiled cleanly — no errors found.`,
    "─".repeat(60),
  ].join("\n");
}

function renderDiagnosis(
  d: OkResult["diagnosis"],
  filePath: string
): string {
  const name = path.basename(filePath);
  const modeTag = d.analysis_mode === "deterministic" ? "[rule-based]" : "[Bob AI]";
  const lineTag = d.evidence.line != null ? `line ${d.evidence.line}` : "unknown line";

  const lines: string[] = [
    "─".repeat(60),
    `  C++ DIAGNOSTIC  —  ${name}`,
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
    lines.push(
      "",
      "  EVIDENCE",
      `    ${lineTag}:  ${d.evidence.code.trim()}`
    );
  }

  lines.push(
    "",
    "  WHAT TO CHECK",
    `    ${d.what_to_check}`,
    "",
    "  SUGGESTION",
    `    ${d.suggestion}`,
    "─".repeat(60)
  );

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
// VS Code Problems panel integration
// ---------------------------------------------------------------------------

/**
 * Push one vscode.Diagnostic for the first error.
 *
 * GCC evidence.line is 1-based; VS Code Range is 0-based.
 * The range covers the whole flagged line so the squiggly is always visible.
 */
function applyVscodeDiagnostic(
  uri: vscode.Uri,
  d: OkResult["diagnosis"]
): void {
  const collection = getDiagnosticCollection();
  collection.clear();

  const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
  // Highlight the full line (col 0 → large col).
  const range = new vscode.Range(evidenceLine, 0, evidenceLine, 9999);

  const modeTag = d.analysis_mode === "deterministic" ? "[rule-based]" : "[Bob AI]";
  // Primary message shown in Problems panel and inline.
  const message =
    `[${d.error_type}] ${d.compiler_message}  —  ${d.compiler_explanation}  ${modeTag}`;

  const diag = new vscode.Diagnostic(range, message, vscode.DiagnosticSeverity.Error);
  diag.source = "C++ Diagnostic";
  // Related information: suggestion, so it appears in the Problems detail.
  diag.relatedInformation = [
    new vscode.DiagnosticRelatedInformation(
      new vscode.Location(uri, range),
      `Suggestion: ${d.suggestion}`
    ),
  ];

  collection.set(uri, [diag]);
}

// ---------------------------------------------------------------------------
// Editor decoration (evidence-line highlight)
// ---------------------------------------------------------------------------

function applyDecoration(
  editor: vscode.TextEditor,
  d: OkResult["diagnosis"]
): void {
  const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
  const range = new vscode.Range(evidenceLine, 0, evidenceLine, 9999);
  editor.setDecorations(getDecorationType(), [{ range }]);
}

function clearDecorations(editor: vscode.TextEditor): void {
  editor.setDecorations(getDecorationType(), []);
}

// ---------------------------------------------------------------------------
// Core: invoke the Python adapter
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

    const proc = cp.spawn(python, args, { cwd: rootDir });

    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

    proc.on("error", (err: NodeJS.ErrnoException) => {
      if (err.code === "ENOENT") {
        resolve({
          status: "error",
          message:
            `Python interpreter not found: "${python}". ` +
            `Set cppDiagnostic.pythonPath in VS Code settings.`,
        });
      } else {
        resolve({ status: "error", message: `Failed to start adapter: ${err.message}` });
      }
    });

    proc.on("close", () => {
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
// Command handler
// ---------------------------------------------------------------------------

async function runDiagnose(context: vscode.ExtensionContext): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("C++ Diagnostic: no active editor.");
    return;
  }

  const doc = editor.document;
  const ext = path.extname(doc.fileName).toLowerCase();
  if (![".cpp", ".cc", ".cxx", ".c++"].includes(ext)) {
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

  const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
  const python: string = cfg.get<string>("pythonPath") ?? "python";
  const root = repoRoot(context);

  const result = await runAdapter(doc.fileName, root, python);
  const uri = doc.uri;
  const uriKey = uri.toString();

  channel.clear();

  switch (result.status) {
    case "clean":
      // Clear all previous markers.
      getDiagnosticCollection().delete(uri);
      clearDecorations(editor);
      lastDiagnosis.delete(uriKey);
      channel.appendLine(renderClean(doc.fileName));
      break;

    case "ok": {
      const d = result.diagnosis;
      lastDiagnosis.set(uriKey, d);

      // 1. Problems panel + squiggly underline.
      applyVscodeDiagnostic(uri, d);

      // 2. Evidence-line background highlight.
      applyDecoration(editor, d);

      // 3. Output channel full-text (fallback / readable summary).
      channel.appendLine(renderDiagnosis(d, doc.fileName));
      break;
    }

    case "error":
      getDiagnosticCollection().delete(uri);
      clearDecorations(editor);
      lastDiagnosis.delete(uriKey);
      channel.appendLine(renderError(result.message));
      vscode.window.showErrorMessage(`C++ Diagnostic: ${result.message}`);
      break;
  }
}

// ---------------------------------------------------------------------------
// Hover provider — explains the error when the user hovers over flagged line
// ---------------------------------------------------------------------------

/**
 * Returns a Markdown hover when the cursor is on the evidence line of the
 * last diagnosis for that document.
 */
function buildHoverProvider(): vscode.HoverProvider {
  return {
    provideHover(
      document: vscode.TextDocument,
      position: vscode.Position
    ): vscode.Hover | undefined {
      const d = lastDiagnosis.get(document.uri.toString());
      if (!d) {
        return undefined;
      }

      const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
      if (position.line !== evidenceLine) {
        return undefined;
      }

      const modeTag =
        d.analysis_mode === "deterministic" ? "rule-based" : "Bob AI";

      const md = new vscode.MarkdownString(undefined, true);
      md.isTrusted = false;

      md.appendMarkdown(`### C++ Diagnostic — \`${d.error_type}\` *(${modeTag})*\n\n`);
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

      const range = new vscode.Range(evidenceLine, 0, evidenceLine, 9999);
      return new vscode.Hover(md, range);
    },
  };
}

// ---------------------------------------------------------------------------
// Extension lifecycle
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
  // Register the main command.
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "cppDiagnostic.diagnose",
      () => runDiagnose(context)
    )
  );

  // Hover provider for C++ files.
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

  // Clear markers when the document is closed or modified.
  context.subscriptions.push(
    vscode.workspace.onDidCloseTextDocument((doc) => {
      getDiagnosticCollection().delete(doc.uri);
      lastDiagnosis.delete(doc.uri.toString());
    })
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((event) => {
      const uriKey = event.document.uri.toString();
      if (!lastDiagnosis.has(uriKey)) {
        return;
      }
      // Source changed — stale diagnostics would be misleading; clear them.
      getDiagnosticCollection().delete(event.document.uri);
      lastDiagnosis.delete(uriKey);
      // Clear the decoration on any visible editor showing this document.
      for (const editor of vscode.window.visibleTextEditors) {
        if (editor.document.uri.toString() === uriKey) {
          clearDecorations(editor);
        }
      }
    })
  );
}

export function deactivate(): void {
  outputChannel?.dispose();
  diagnosticCollection?.dispose();
  decorationType?.dispose();
  lastDiagnosis.clear();
}
