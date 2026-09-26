export default function CodeEditor({ value, onChange }) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={10}
      cols={80}
      placeholder="Paste source code here"
    />
  );
}
