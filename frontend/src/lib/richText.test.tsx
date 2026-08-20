import { render } from "@testing-library/react";
import { expect, test } from "vitest";

import { RichDetails, renderInline, splitSections } from "./richText";

/* ---- splitSections ------------------------------------------------------ */

test("splits on ' // ': first section is the description, later ones are labelled", () => {
  const sections = splitSections(
    "The gap d between consecutive terms. // Formula: [Katex]d = a_{n+1} - a_n[/Katex] // Example: 2, 5, 8 has d = 3",
  );
  expect(sections).toEqual([
    { label: null, text: "The gap d between consecutive terms." },
    { label: "Formula", text: "[Katex]d = a_{n+1} - a_n[/Katex]" },
    { label: "Example", text: "2, 5, 8 has d = 3" },
  ]);
});

test("a details string with no separator is one unlabelled section", () => {
  expect(splitSections("Just a description.")).toEqual([
    { label: null, text: "Just a description." },
  ]);
});

test("a description containing a colon does not grow a label", () => {
  // Only sections AFTER the first carry the "Label:" convention.
  expect(splitSections("Note: this whole thing is the description.")).toEqual([
    { label: null, text: "Note: this whole thing is the description." },
  ]);
});

test("a later section starting with markup instead of a label stays intact", () => {
  const sections = splitSections(
    "Desc // [see this](https://example.com/a) is the whole section",
  );
  expect(sections[1]).toEqual({
    label: null,
    text: "[see this](https://example.com/a) is the whole section",
  });
});

/* ---- renderInline (via rendered output — no innerHTML anywhere) --------- */

test("katex becomes a code node carrying the raw LaTeX", () => {
  const { container } = render(
    <div>{renderInline("Sum: [Katex]S_n = \\frac{n}{2}(a + l)[/Katex] done")}</div>,
  );
  const code = container.querySelector("code.katex-inline");
  expect(code).not.toBeNull();
  expect(code!.textContent).toBe("S_n = \\frac{n}{2}(a + l)");
  expect(container.textContent).toContain("Sum: ");
  expect(container.textContent).toContain(" done");
});

test("an https image renders as <img> with alt and lazy loading", () => {
  const { container } = render(
    <div>
      {renderInline('See [img src="https://cdn.example/ap.png" alt="AP number line"].')}
    </div>,
  );
  const img = container.querySelector("img");
  expect(img).not.toBeNull();
  expect(img!.getAttribute("src")).toBe("https://cdn.example/ap.png");
  expect(img!.getAttribute("alt")).toBe("AP number line");
  expect(img!.getAttribute("loading")).toBe("lazy");
});

test("an http image is NOT rendered — the literal text stays visible", () => {
  const literal = '[img src="http://cdn.example/ap.png" alt="AP"]';
  const { container } = render(<div>{renderInline(`See ${literal}.`)}</div>);
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toBe(`See ${literal}.`);
});

test("an https link renders as a safe anchor", () => {
  const { container } = render(
    <div>{renderInline("Read [the derivation](https://example.com/derivation) first.")}</div>,
  );
  const anchor = container.querySelector("a");
  expect(anchor).not.toBeNull();
  expect(anchor!.getAttribute("href")).toBe("https://example.com/derivation");
  expect(anchor!.getAttribute("target")).toBe("_blank");
  expect(anchor!.getAttribute("rel")).toBe("noreferrer");
  expect(anchor!.textContent).toBe("the derivation");
});

test("an http link stays literal text", () => {
  const literal = "[docs](http://example.com/x)";
  const { container } = render(<div>{renderInline(literal)}</div>);
  expect(container.querySelector("a")).toBeNull();
  expect(container.textContent).toBe(literal);
});

test("plain text passes through untouched — including angle brackets", () => {
  // No HTML parsing: markup-looking source text renders as text, not markup.
  const text = "a < b and <script>alert(1)</script> is just text";
  const { container } = render(<div>{renderInline(text)}</div>);
  expect(container.textContent).toBe(text);
  expect(container.querySelector("script")).toBeNull();
});

/* ---- RichDetails --------------------------------------------------------- */

test("RichDetails renders titled blocks with inline markup transformed", () => {
  const details =
    "The gap d. // Formula: [Katex]d = a_2 - a_1[/Katex] // " +
    'Figure: [img src="https://cdn.example/f.png" alt="fig"]';
  const { container } = render(<RichDetails details={details} />);
  const sections = container.querySelectorAll(".rich-section");
  expect(sections.length).toBe(3);
  expect(sections[0].querySelector(".rich-section-label")).toBeNull();
  expect(sections[0].textContent).toBe("The gap d.");
  expect(sections[1].querySelector(".rich-section-label")!.textContent).toBe("Formula");
  expect(sections[1].querySelector("code.katex-inline")!.textContent).toBe("d = a_2 - a_1");
  expect(sections[2].querySelector("img")!.getAttribute("src")).toBe("https://cdn.example/f.png");
});
