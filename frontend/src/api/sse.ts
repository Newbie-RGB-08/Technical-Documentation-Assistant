export interface ParsedSSE {
  event: string;
  data: any;
}

export function parseSSEBlock(block: string): ParsedSSE | null {
  if (!block.trim()) return null;
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}

export function createSSEParser(onEvent: (event: ParsedSSE) => void) {
  let buffer = "";
  return {
    push(chunk: string) {
      buffer += chunk.replace(/\r\n/g, "\n");
      let idx = buffer.indexOf("\n\n");
      while (idx >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const parsed = parseSSEBlock(block);
        if (parsed) onEvent(parsed);
        idx = buffer.indexOf("\n\n");
      }
    },
    flush() {
      const parsed = parseSSEBlock(buffer);
      if (parsed) onEvent(parsed);
      buffer = "";
    },
  };
}
