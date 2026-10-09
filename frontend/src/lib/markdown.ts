/**
 * Markdown for the long free-text fields of a card — its description and its
 * `multiline_text` attributes.
 *
 * Content imported from another tool often arrives as markdown, and people
 * type lists and headings into a description anyway. `renderMarkdown` turns
 * that text into HTML and hands it to `sanitizeRichHtml` (`lib/richHtml.ts`),
 * so a rendered description obeys the same policy as stored rich text: bare
 * `http(s)://` addresses become links, every link opens in a new tab, and no
 * other href survives.
 *
 * The parser is configured for text that was not necessarily written as
 * markdown:
 *   - `html: false` — a typed `<tag>` shows as text and is never parsed.
 *   - `breaks: true` — a single line break stays a line break, which is how
 *     these fields were displayed before (`white-space: pre-wrap`).
 *   - no indented code blocks — a line indented by four spaces is far more
 *     often an indented note than code. Fenced blocks still work.
 *   - no images — a description must not load a remote resource on view, so
 *     `![alt](url)` renders as a link to the image.
 */
import MarkdownIt from "markdown-it";

import { sanitizeRichHtml } from "./richHtml";

const md = new MarkdownIt({ html: false, breaks: true, linkify: false }).disable([
  "code",
  "image",
]);

/** Sanitised HTML for `dangerouslySetInnerHTML`. Empty input gives `""`. */
export function renderMarkdown(text: string | null | undefined): string {
  if (!text) return "";
  return sanitizeRichHtml(md.render(text));
}
