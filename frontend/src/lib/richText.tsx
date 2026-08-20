import type { ReactNode } from "react";

/**
 * Pure rendering of the house `concept_details` format.
 *
 * The string is " // "-joined sections: the first is the description, later
 * ones carry a "Label:" prefix. Inside a section, three inline tokens exist:
 *
 *   [Katex] ... [/Katex]              raw LaTeX (typeset later — recorded
 *                                     follow-up; shown verbatim in <code>)
 *   [img src="https://…" alt="…"]     an image, https only
 *   [text](https://url)               a link, https only
 *
 * Everything is built as React elements from plain strings — no HTML parsing,
 * no innerHTML — so source content can never inject markup (XSS is
 * structurally impossible). A token that does not qualify (http image, http
 * link) is rendered as its literal text rather than dropped: the reviewer
 * must see exactly what the release carries.
 *
 * This module makes no judgment about content (Rule 1): it only parses the
 * pipeline's own markup, which is mechanics, not meaning.
 */

export interface DetailSection {
  /** null for the leading description section (and any unlabelled section). */
  label: string | null;
  text: string;
}

const SECTION_SEPARATOR = " // ";

/**
 * A later section's label: everything up to the first ":", provided it is not
 * markup (no brackets — "[link](https://x)" must not donate "https" as a
 * label). A later section without a well-formed label renders unlabelled,
 * text intact.
 */
const LABEL_RE = /^([^:[\]]+):\s*/;

export function splitSections(details: string): DetailSection[] {
  return details.split(SECTION_SEPARATOR).map((part, index) => {
    if (index > 0) {
      const match = LABEL_RE.exec(part);
      if (match) return { label: match[1], text: part.slice(match[0].length) };
    }
    return { label: null, text: part };
  });
}

// One alternation, matched left to right. [Katex] must precede the generic
// link pattern and [img …] is distinguished by its attribute shape. Capture
// groups: 1 = katex body, 2/3 = img src/alt, 4/5 = link text/url.
const TOKEN_RE =
  /\[Katex\]([\s\S]*?)\[\/Katex\]|\[img src="([^"]*)" alt="([^"]*)"\]|\[([^\]]*)\]\(([^()\s]+)\)/g;

export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  TOKEN_RE.lastIndex = 0;
  for (let m = TOKEN_RE.exec(text); m !== null; m = TOKEN_RE.exec(text)) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const matched = m[0];
    const [, katex, imgSrc, imgAlt, linkText, linkUrl] = m;
    if (katex !== undefined) {
      nodes.push(
        <code className="katex-inline" key={key++}>
          {katex}
        </code>,
      );
    } else if (imgSrc !== undefined) {
      if (imgSrc.startsWith("https://")) {
        nodes.push(<img src={imgSrc} alt={imgAlt} loading="lazy" key={key++} />);
      } else {
        nodes.push(matched);
      }
    } else if (linkUrl !== undefined && linkUrl.startsWith("https://")) {
      nodes.push(
        <a href={linkUrl} target="_blank" rel="noreferrer" key={key++}>
          {linkText}
        </a>,
      );
    } else {
      nodes.push(matched);
    }
    last = m.index + matched.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** The full concept_details string as titled blocks of React nodes. */
export function RichDetails({ details }: { details: string }) {
  return (
    <div className="rich-details">
      {splitSections(details).map((section, index) => (
        <div className="rich-section" key={index}>
          {section.label !== null && (
            <div className="rich-section-label">{section.label}</div>
          )}
          <div className="rich-section-body">{renderInline(section.text)}</div>
        </div>
      ))}
    </div>
  );
}
