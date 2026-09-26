export async function diagnose(payload) {
  return {
    diagnosis: {
      source: "frontend-placeholder",
      message: "Connect to backend /diagnose endpoint.",
    },
    request: payload,
  };
}
