/* The Aegis mark: the Constellation Shield (owner's pick, gallery #3).
   Five nodes trace a heater-shield silhouette and an amber hub links the
   corners and the apex — the concept map itself is the armor. Decorative
   (aria-hidden): the brand NAME stays a real text node next to it, which
   both accessibility and the App test contract ("Aegis" must render as its
   own text) depend on.

   The graph inherits currentColor (the brand accent in both themes); the
   hub keeps its amber literal, which reads on light and dark grounds. */

export default function Logo({ className = "brand-mark" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      aria-hidden="true"
      focusable="false"
    >
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <line x1="13" y1="12" x2="51" y2="12" />
        <line x1="13" y1="12" x2="16" y2="31.5" />
        <line x1="51" y1="12" x2="48" y2="31.5" />
        <line x1="16" y1="31.5" x2="32" y2="53" />
        <line x1="48" y1="31.5" x2="32" y2="53" />
        <line x1="32" y1="27" x2="13" y2="12" />
        <line x1="32" y1="27" x2="51" y2="12" />
        <line x1="32" y1="27" x2="32" y2="53" />
      </g>
      <g fill="currentColor">
        <circle cx="13" cy="12" r="4.75" />
        <circle cx="51" cy="12" r="4.75" />
        <circle cx="16" cy="31.5" r="4.75" />
        <circle cx="48" cy="31.5" r="4.75" />
        <circle cx="32" cy="53" r="4.75" />
      </g>
      <circle cx="32" cy="27" r="6" fill="#F59E0B" />
    </svg>
  );
}
