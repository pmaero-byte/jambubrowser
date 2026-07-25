import { describe, it, expect } from "vitest";
import {
  BookmarkEntry, toNetscapeBookmarksHtml, parseNetscapeBookmarksHtml, mergeBookmarks,
} from "./bookmarksIO";

function bm(url: string, title = url, folder = "Other", addedAt = 1_700_000_000_000): BookmarkEntry {
  return { id: `id-${url}`, url, title, folder, addedAt };
}

describe("toNetscapeBookmarksHtml", () => {
  it("emits the standard Netscape header", () => {
    const html = toNetscapeBookmarksHtml([]);
    expect(html).toContain("<!DOCTYPE NETSCAPE-Bookmark-file-1>");
    expect(html).toContain("<H1>Bookmarks</H1>");
  });

  it("groups bookmarks by folder and writes ADD_DATE in seconds", () => {
    const html = toNetscapeBookmarksHtml([
      bm("https://a.example", "A", "Music", 1_700_000_000_000),
      bm("https://b.example", "B", "Dev", 1_700_000_000_000),
      bm("https://c.example", "C", "Music", 1_700_000_000_000),
    ]);
    expect(html).toContain("<H3>Music</H3>");
    expect(html).toContain("<H3>Dev</H3>");
    expect(html).toContain('<A HREF="https://a.example" ADD_DATE="1700000000">A</A>');
    // Folder order follows first appearance: Music before Dev.
    expect(html.indexOf("<H3>Music</H3>")).toBeLessThan(html.indexOf("<H3>Dev</H3>"));
  });

  it("escapes HTML special characters in titles and URLs", () => {
    const html = toNetscapeBookmarksHtml([
      bm("https://x.example/?a=1&b=2", 'Tom & "Jerry" <3'),
    ]);
    expect(html).toContain("Tom &amp; &quot;Jerry&quot; &lt;3");
    expect(html).toContain("https://x.example/?a=1&amp;b=2");
  });
});

describe("parseNetscapeBookmarksHtml", () => {
  it("parses entries with folder and ADD_DATE", () => {
    const html = toNetscapeBookmarksHtml([
      bm("https://a.example", "A", "Music", 1_700_000_000_000),
      bm("https://b.example", "B"),
    ]);
    const parsed = parseNetscapeBookmarksHtml(html);
    expect(parsed).toEqual([
      { url: "https://a.example", title: "A", folder: "Music", addedAt: 1_700_000_000_000 },
      { url: "https://b.example", title: "B", folder: "Other", addedAt: 1_700_000_000_000 },
    ]);
  });

  it("round-trips titles containing escaped entities", () => {
    const original = [bm("https://x.example/?a=1&b=2", 'Tom & "Jerry" <3')];
    const parsed = parseNetscapeBookmarksHtml(toNetscapeBookmarksHtml(original));
    expect(parsed[0].title).toBe('Tom & "Jerry" <3');
    expect(parsed[0].url).toBe("https://x.example/?a=1&b=2");
  });

  it("parses a hand-written Chrome-style export with nested folders", () => {
    const html = `<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
  <DT><H3>Bookmarks bar</H3>
  <DL><p>
    <DT><A HREF="https://one.example" ADD_DATE="1700000000">One</A>
    <DT><H3>Nested</H3>
    <DL><p>
      <DT><A HREF="https://two.example">Two</A>
    </DL><p>
  </DL><p>
</DL><p>`;
    const parsed = parseNetscapeBookmarksHtml(html);
    expect(parsed).toHaveLength(2);
    expect(parsed[0]).toMatchObject({ url: "https://one.example", folder: "Bookmarks bar" });
    expect(parsed[1]).toMatchObject({ url: "https://two.example", folder: "Nested" });
  });

  it("skips anchors without href and javascript: URLs", () => {
    const parsed = parseNetscapeBookmarksHtml(
      `<DL><p><DT><A>No href</A><DT><A HREF="javascript:void(0)">JS</A><DT><A HREF="https://ok.example">OK</A></DL><p>`,
    );
    expect(parsed.map((p) => p.url)).toEqual(["https://ok.example"]);
  });
});

describe("mergeBookmarks", () => {
  it("prepends new bookmarks and reports the added count", () => {
    const { merged, added } = mergeBookmarks(
      [bm("https://a.example")],
      [{ url: "https://b.example", title: "B", folder: "Other", addedAt: 1 }],
    );
    expect(added).toBe(1);
    expect(merged.map((b) => b.url)).toEqual(["https://b.example", "https://a.example"]);
    expect(merged[0].id).toBeTruthy();
  });

  it("dedupes by URL against existing bookmarks and within the import", () => {
    const { merged, added } = mergeBookmarks(
      [bm("https://a.example")],
      [
        { url: "https://a.example", title: "dupe", folder: "Other", addedAt: 1 },
        { url: "https://b.example", title: "B", folder: "Other", addedAt: 1 },
        { url: "https://b.example", title: "B again", folder: "Other", addedAt: 2 },
      ],
    );
    expect(added).toBe(1);
    expect(merged).toHaveLength(2);
  });
});
