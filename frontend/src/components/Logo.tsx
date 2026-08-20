/* The Aegis mark. Decorative (aria-hidden) — the brand NAME stays a real
   text node next to it, which both accessibility and the App test contract
   ("Aegis" must render as its own text) depend on.

   The artwork lives in one <path> pair so the mark can be swapped for the
   owner's chosen concept without touching any call site. */

export default function Logo({ className = "brand-mark" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M32 6 L54 14 V32 C54 45 45 54 32 58 C19 54 10 45 10 32 V14 Z"
        fill="currentColor"
        opacity="0.14"
      />
      <path
        d="M32 6 L54 14 V32 C54 45 45 54 32 58 C19 54 10 45 10 32 V14 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinejoin="round"
      />
      <path
        d="M22 32 L29 39 L43 25"
        fill="none"
        stroke="currentColor"
        strokeWidth="4.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
