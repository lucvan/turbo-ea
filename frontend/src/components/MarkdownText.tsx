/**
 * A long free-text value rendered as markdown.
 *
 * Unlike `LinkifiedText`, which renders a fragment inside the call site's own
 * `Typography`, markdown produces block elements (paragraphs, lists, tables),
 * so this renders its own `<div>`. Put it where a block may go — a
 * `Typography` hosting it needs `component="div"`. Text colour and font size
 * are inherited from the parent.
 *
 * Use it only for the long fields of a card (description, `multiline_text`
 * attributes). Short single-line text, truncated previews and grid cells stay
 * on `LinkifiedText`.
 */
import { useMemo } from "react";
import Box from "@mui/material/Box";
import type { SxProps, Theme } from "@mui/material/styles";

import { renderMarkdown } from "@/lib/markdown";

export interface MarkdownTextProps {
  text: string | null | undefined;
  sx?: SxProps<Theme>;
}

const baseSx: SxProps<Theme> = {
  // The HTML carries a newline between blocks; under an inherited `pre-wrap`
  // each one would show as an empty line.
  whiteSpace: "normal",
  overflowWrap: "anywhere",
  lineHeight: 1.6,
  // Space sits between blocks only, so the first and last one stay flush
  // with the section. (Sibling selectors, not `:first-child`, which Emotion
  // flags as unsafe.)
  "& > *, & blockquote > *": { mt: 0, mb: 0 },
  "& > * + *, & blockquote > * + *": { mt: 1 },
  "& > * + :is(h1, h2, h3, h4, h5, h6), & > * + hr, & > hr + *": { mt: 2 },
  "& h1, & h2, & h3, & h4, & h5, & h6": {
    color: "text.primary",
    fontWeight: 600,
    lineHeight: 1.3,
  },
  "& h1": { fontSize: "1.15rem" },
  "& h2": { fontSize: "1.05rem" },
  "& h3": { fontSize: "0.95rem" },
  "& h4, & h5, & h6": { fontSize: "0.875rem" },
  "& ul, & ol": { pl: 3 },
  "& li": { mb: 0.25 },
  "& li > p": { my: 0 },
  "& li > ul, & li > ol": { my: 0.25 },
  "& a": {
    color: "primary.main",
    textDecoration: "none",
    "&:hover": { textDecoration: "underline" },
  },
  "& blockquote": {
    mx: 0,
    pl: 1.5,
    borderLeft: 3,
    borderColor: "divider",
  },
  "& code": {
    fontFamily: "monospace",
    fontSize: "0.85em",
    bgcolor: "action.hover",
    px: 0.5,
    py: 0.15,
    borderRadius: 0.5,
  },
  "& pre": {
    p: 1.5,
    bgcolor: "action.hover",
    borderRadius: 1,
    overflowX: "auto",
    whiteSpace: "pre",
  },
  "& pre code": { bgcolor: "transparent", p: 0 },
  "& hr": { border: 0, borderTop: 1, borderColor: "divider" },
  // A wide table scrolls inside the section, never the page.
  "& table": {
    display: "block",
    overflowX: "auto",
    maxWidth: "100%",
    borderCollapse: "collapse",
  },
  "& th, & td": {
    border: 1,
    borderColor: "divider",
    px: 1,
    py: 0.5,
    textAlign: "start",
    verticalAlign: "top",
  },
  "& th": { color: "text.primary", fontWeight: 600 },
};

export default function MarkdownText({ text, sx }: MarkdownTextProps) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  if (!html) return null;
  return (
    <Box
      className="markdown-text"
      sx={[baseSx, ...(Array.isArray(sx) ? sx : [sx])] as SxProps<Theme>}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
