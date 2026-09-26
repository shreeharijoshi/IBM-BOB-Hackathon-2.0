"use strict";
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
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const cp = __importStar(require("child_process"));
const path = __importStar(require("path"));
// ---------------------------------------------------------------------------
// Module-level state (one set per extension lifetime)
// ---------------------------------------------------------------------------
let outputChannel;
let diagnosticCollection;
/** Decoration type for the evidence-line highlight.  Created once. */
let decorationType;
/**
 * The last successful diagnosis keyed by file URI string.
 * The hover provider reads from this map.
 */
const lastDiagnosis = new Map();
// ---------------------------------------------------------------------------
// Lazy accessors
// ---------------------------------------------------------------------------
function getChannel() {
    if (!outputChannel) {
        outputChannel = vscode.window.createOutputChannel("C++ Diagnostic");
    }
    return outputChannel;
}
function getDiagnosticCollection() {
    if (!diagnosticCollection) {
        diagnosticCollection = vscode.languages.createDiagnosticCollection("cppDiagnostic");
    }
    return diagnosticCollection;
}
function getDecorationType() {
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
function repoRoot(context) {
    return path.resolve(context.extensionPath, "..");
}
// ---------------------------------------------------------------------------
// Helpers: Output Channel rendering (fallback / full-text view)
// ---------------------------------------------------------------------------
function renderClean(filePath) {
    const name = path.basename(filePath);
    return [
        "─".repeat(60),
        `  ✓  ${name}  compiled cleanly — no errors found.`,
        "─".repeat(60),
    ].join("\n");
}
function renderDiagnosis(d, filePath) {
    const name = path.basename(filePath);
    const modeTag = d.analysis_mode === "deterministic" ? "[rule-based]" : "[Bob AI]";
    const lineTag = d.evidence.line != null ? `line ${d.evidence.line}` : "unknown line";
    const lines = [
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
        lines.push("", "  EVIDENCE", `    ${lineTag}:  ${d.evidence.code.trim()}`);
    }
    lines.push("", "  WHAT TO CHECK", `    ${d.what_to_check}`, "", "  SUGGESTION", `    ${d.suggestion}`, "─".repeat(60));
    return lines.join("\n");
}
function renderError(message) {
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
function applyVscodeDiagnostic(uri, d) {
    const collection = getDiagnosticCollection();
    collection.clear();
    const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
    // Highlight the full line (col 0 → large col).
    const range = new vscode.Range(evidenceLine, 0, evidenceLine, 9999);
    const modeTag = d.analysis_mode === "deterministic" ? "[rule-based]" : "[Bob AI]";
    // Primary message shown in Problems panel and inline.
    const message = `[${d.error_type}] ${d.compiler_message}  —  ${d.compiler_explanation}  ${modeTag}`;
    const diag = new vscode.Diagnostic(range, message, vscode.DiagnosticSeverity.Error);
    diag.source = "C++ Diagnostic";
    // Related information: suggestion, so it appears in the Problems detail.
    diag.relatedInformation = [
        new vscode.DiagnosticRelatedInformation(new vscode.Location(uri, range), `Suggestion: ${d.suggestion}`),
    ];
    collection.set(uri, [diag]);
}
// ---------------------------------------------------------------------------
// Editor decoration (evidence-line highlight)
// ---------------------------------------------------------------------------
function applyDecoration(editor, d) {
    const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
    const range = new vscode.Range(evidenceLine, 0, evidenceLine, 9999);
    editor.setDecorations(getDecorationType(), [{ range }]);
}
function clearDecorations(editor) {
    editor.setDecorations(getDecorationType(), []);
}
// ---------------------------------------------------------------------------
// Core: invoke the Python adapter
// ---------------------------------------------------------------------------
function runAdapter(filePath, rootDir, python) {
    return new Promise((resolve) => {
        const args = [
            path.join(rootDir, "backend", "diagnose.py"),
            "--file",
            filePath,
        ];
        let stdout = "";
        let stderr = "";
        const proc = cp.spawn(python, args, { cwd: rootDir });
        proc.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
        proc.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
        proc.on("error", (err) => {
            if (err.code === "ENOENT") {
                resolve({
                    status: "error",
                    message: `Python interpreter not found: "${python}". ` +
                        `Set cppDiagnostic.pythonPath in VS Code settings.`,
                });
            }
            else {
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
                resolve(JSON.parse(raw));
            }
            catch {
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
async function runDiagnose(context) {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage("C++ Diagnostic: no active editor.");
        return;
    }
    const doc = editor.document;
    const ext = path.extname(doc.fileName).toLowerCase();
    if (![".cpp", ".cc", ".cxx", ".c++"].includes(ext)) {
        vscode.window.showWarningMessage("C++ Diagnostic: active file is not a C++ source file (.cpp / .cc / .cxx).");
        return;
    }
    if (doc.isDirty) {
        await doc.save();
    }
    const channel = getChannel();
    channel.show(true);
    channel.appendLine(`\nDiagnosing ${path.basename(doc.fileName)} …`);
    const cfg = vscode.workspace.getConfiguration("cppDiagnostic");
    const python = cfg.get("pythonPath") ?? "python";
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
function buildHoverProvider() {
    return {
        provideHover(document, position) {
            const d = lastDiagnosis.get(document.uri.toString());
            if (!d) {
                return undefined;
            }
            const evidenceLine = d.evidence.line != null ? d.evidence.line - 1 : 0;
            if (position.line !== evidenceLine) {
                return undefined;
            }
            const modeTag = d.analysis_mode === "deterministic" ? "rule-based" : "Bob AI";
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
function activate(context) {
    // Register the main command.
    context.subscriptions.push(vscode.commands.registerCommand("cppDiagnostic.diagnose", () => runDiagnose(context)));
    // Hover provider for C++ files.
    context.subscriptions.push(vscode.languages.registerHoverProvider([
        { language: "cpp" },
        { pattern: "**/*.cpp" },
        { pattern: "**/*.cc" },
        { pattern: "**/*.cxx" },
    ], buildHoverProvider()));
    // Clear markers when the document is closed or modified.
    context.subscriptions.push(vscode.workspace.onDidCloseTextDocument((doc) => {
        getDiagnosticCollection().delete(doc.uri);
        lastDiagnosis.delete(doc.uri.toString());
    }));
    context.subscriptions.push(vscode.workspace.onDidChangeTextDocument((event) => {
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
    }));
}
function deactivate() {
    outputChannel?.dispose();
    diagnosticCollection?.dispose();
    decorationType?.dispose();
    lastDiagnosis.clear();
}
//# sourceMappingURL=extension.js.map