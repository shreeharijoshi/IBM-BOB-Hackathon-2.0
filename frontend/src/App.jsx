import { useState } from "react";

import { diagnose } from "./api";
import CodeEditor from "./components/CodeEditor";
import DiagnosticCard from "./components/DiagnosticCard";

export default function App() {
  const [sourceCode, setSourceCode] = useState("");
  const [result, setResult] = useState(null);

  async function runDiagnosis() {
    setResult(await diagnose({ source_code: sourceCode, compiler_output: "" }));
  }

  return (
    <main>
      <h1>Context Diagnostic</h1>
      <CodeEditor value={sourceCode} onChange={setSourceCode} />
      <button onClick={runDiagnosis}>Analyze</button>
      <DiagnosticCard diagnosis={result?.diagnosis} />
    </main>
  );
}
