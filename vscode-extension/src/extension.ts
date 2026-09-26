/**
 * C++ Diagnostic — VS Code extension entry point.
 *
 * Registers the "Diagnose C++" command.  When invoked, the extension:
 *   1. Resolves the active C++ editor.
 *   2. Saves the file (so the Python adapter reads the current content).
 *   3. Spawns `python backend/diagnose.py --file <path>` from the workspace root.
 *   4. Parses the JSON result written to stdout by the adapter.
 *   5. Formats and displays the diagnosis in a dedicated Output Channel.
 *
 * All three outcome states are handled:
 *   • "clean"  — compilation succeeded with no errors.
 *   • "ok"     — first compiler error analysed and explained.
 *   • "error"  — infrastructure failure (GCC missing, timeout, unreadable file).
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as path from "path";

// A single output channel shared across all invocations.
let outputChannel: vscode.OutputChannel | undefined;

function getChannel(): vscode.OutputChannel {
  if (!outputChannel) {
    outputChannel = vscode.window.createOutputChannel("C++ Diagnostic");
  }
  return outputChannel;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return the absolute path to the repository root (one level above the
 *  extension directory so `backend/diagnose.py` is resolvable). */
function repoRoot(context: vscode.ExtensionContext): string {
  // The extension lives at  <repo>/vscode-extension/
  // so __dirname at runtime is  <repo>/vscode-extension/out/
  return path.resolve(context.extensionPath, "..");
}

/** Render a clean-compile message. */
function renderClean(filePath: string): string {
  const name = path.basename(filePath);
  return [
    "─".repeat(60),
    `  ✓  ${name}  compiled cleanly — no errors found.`,
    "─".repeat(60),
  ].join("\n");
}

/** Render a full diagnosis. */
function renderDiagnosis(
  d: {
    error_type: string;
    analysis_mode: string;
    compiler_message: string;
    compiler_explanation: string;
    source_explanation: string;
    evidence: { line: number | null; code: string };
    what_to_check: string;
    suggestion: string;
  },
  filePath: string
): string {
  const name = path.basename(filePath);
  const modeTag = d.analysis_mode === "deterministic" ? "[rule-based]" : "[Bob AI]";
  const lineTag =
    d.evidence.line != null ? `line ${d.evidence.line}` : "unknown line";

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

/** Render an infrastructure / adapter error. */
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
// Core: invoke the Python adapter
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
          message: `Python interpreter not found: "${python}". ` +
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
        const parsed = JSON.parse(raw) as DiagnoseResult;
        resolve(parsed);
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

  // Save so the adapter reads the latest version from disk.
  if (doc.isDirty) {
    await doc.save();
  }

  const channel = getChannel();
  channel.show(true);          // reveal without stealing focus
  channel.appendLine(`\nDiagnosing ${path.basename(doc.fileName)} …`);

  const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
  const python: string = cfg.get<string>("pythonPath") ?? "python";
  const root = repoRoot(context);

  const result = await runAdapter(doc.fileName, root, python);

  channel.clear();

  switch (result.status) {
    case "clean":
      channel.appendLine(renderClean(doc.fileName));
      break;

    case "ok":
      channel.appendLine(renderDiagnosis(result.diagnosis, doc.fileName));
      break;

    case "error":
      channel.appendLine(renderError(result.message));
      vscode.window.showErrorMessage(`C++ Diagnostic: ${result.message}`);
      break;
  }
}

// ---------------------------------------------------------------------------
// Extension lifecycle
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
  const disposable = vscode.commands.registerCommand(
    "cppDiagnostic.diagnose",
    () => runDiagnose(context)
  );
  context.subscriptions.push(disposable);
}

export function deactivate(): void {
  if (outputChannel) {
    outputChannel.dispose();
  }
}
