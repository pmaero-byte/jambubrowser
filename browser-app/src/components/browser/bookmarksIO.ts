// ── Bookmarks import/export ──────────────────────────────────────
// Pure helpers for the standard Netscape bookmark-file format (what
// Chrome/Firefox/Edge produce and consume). Serialization is string
// building; parsing uses DOMParser, which exists both in the app and in
// jsdom (vitest).

export interface BookmarkEntry {
  id: string;
  url: string;
  title: string;
  folder: string;
  addedAt: number;
}

/** Bookmark fields recoverable from a Netscape HTML file (no local id). */
export type ImportedBookmark = Omit<BookmarkEntry, "id">;

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Serialize bookmarks to a Netscape-bookmarks HTML file, grouping entries
 * by folder under `<H3>` headings (folder order = first appearance).
 * ADD_DATE is seconds since the epoch, per the format's convention.
 */
export function toNetscapeBookmarksHtml(bookmarks: BookmarkEntry[]): string {
  const folders: string[] = [];
  for (const b of bookmarks) {
    if (!folders.includes(b.folder)) folders.push(b.folder);
  }
  const lines: string[] = [
    "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
    '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
    "<TITLE>Bookmarks</TITLE>",
    "<H1>Bookmarks</H1>",
    "<DL><p>",
  ];
  for (const folder of folders) {
    lines.push(`    <DT><H3>${escapeHtml(folder)}</H3>`);
    lines.push("    <DL><p>");
    for (const b of bookmarks.filter((x) => x.folder === folder)) {
      const addDate = Math.floor(b.addedAt / 1000);
      lines.push(`        <DT><A HREF="${escapeHtml(b.url)}" ADD_DATE="${addDate}">${escapeHtml(b.title)}</A>`);
    }
    lines.push("    </DL><p>");
  }
  lines.push("</DL><p>");
  return lines.join("\n") + "\n";
}

/**
 * Parse a Netscape-bookmarks HTML file. Walks H3/A elements in document
 * order so nested folder structure flattens to the nearest enclosing
 * folder heading — matching how browsers treat the format. Entries
 * without a recognizable folder land in "Other".
 */
export function parseNetscapeBookmarksHtml(html: string): ImportedBookmark[] {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const out: ImportedBookmark[] = [];
  let folder = "Other";
  for (const el of Array.from(doc.querySelectorAll("h3, a"))) {
    if (el.tagName === "H3") {
      const name = (el.textContent || "").trim();
      if (name) folder = name;
    } else {
      const url = el.getAttribute("href") || "";
      if (!url || url.startsWith("javascript:")) continue;
      const addDate = parseInt(el.getAttribute("ADD_DATE") || "", 10);
      out.push({
        url,
        title: (el.textContent || "").trim() || url,
        folder,
        addedAt: Number.isFinite(addDate) ? addDate * 1000 : Date.now(),
      });
    }
  }
  return out;
}

/**
 * Merge imported bookmarks into the existing list, skipping URLs that
 * are already bookmarked. Imported entries are prepended (newest first),
 * matching how toggleBookmark adds entries.
 */
export function mergeBookmarks(
  existing: BookmarkEntry[],
  imported: ImportedBookmark[],
): { merged: BookmarkEntry[]; added: number } {
  const known = new Set(existing.map((b) => b.url));
  const fresh: BookmarkEntry[] = [];
  for (const b of imported) {
    if (known.has(b.url)) continue; // already bookmarked, or a dupe within the file
    known.add(b.url);
    fresh.push({ ...b, id: crypto.randomUUID() });
  }
  return { merged: [...fresh, ...existing], added: fresh.length };
}
