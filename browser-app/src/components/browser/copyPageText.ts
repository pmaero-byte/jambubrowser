/**
 * Copy the page's visible text to the clipboard.
 *
 * Runs `document.body.innerText` inside the live page through the existing
 * `browser_evaluate` CDP command and writes the result with the WebView
 * clipboard. The engine returns JSON-encoded strings, so a quoted payload
 * is unwrapped before copying.
 */
export async function copyPageText(
  tabId: string,
  evaluate: (tabId: string, expression: string) => Promise<unknown>,
  writeClipboard: (text: string) => Promise<void>,
): Promise<{ chars: number }> {
  const raw = await evaluate(tabId, "document.body ? document.body.innerText : ''");
  let text = typeof raw === "string" ? raw : String(raw ?? "");
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed === "string") text = parsed;
  } catch {
    /* already plain text */
  }
  const trimmed = text.trim();
  await writeClipboard(trimmed);
  return { chars: trimmed.length };
}
