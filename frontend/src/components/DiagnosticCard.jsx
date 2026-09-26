export default function DiagnosticCard({ diagnosis }) {
  if (!diagnosis) return null;
  return (
    <section>
      <h2>Diagnosis</h2>
      <p>
        <strong>{diagnosis.source}:</strong> {diagnosis.message}
      </p>
    </section>
  );
}
