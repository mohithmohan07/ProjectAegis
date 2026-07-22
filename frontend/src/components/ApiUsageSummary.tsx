import type { OpenAIUsage } from "../types";

interface ApiUsageSummaryProps {
  usage?: OpenAIUsage | null;
  compact?: boolean;
  filename?: string;
  fileLabel?: string;
}

const TOKEN_FORMATTER = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

export function formatTokenCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? TOKEN_FORMATTER.format(value)
    : "—";
}

export function formatEstimatedCost(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Unavailable";
  const digits = Math.abs(value) < 0.01 ? 6 : 4;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/**
 * Displays billing-token counts only. It intentionally never accepts or renders
 * an API credential.
 */
export default function ApiUsageSummary({
  usage,
  compact = false,
  filename,
  fileLabel = "Generated file",
}: ApiUsageSummaryProps) {
  if (!usage || (usage.request_count <= 0 && usage.total_tokens <= 0)) return null;

  const costAvailable = usage.estimated_cost_usd != null;
  const model = usage.model || "Unknown model";

  return (
    <section
      className={`api-usage${compact ? " api-usage-compact" : ""}`}
      data-testid="api-usage-summary"
      aria-label="OpenAI API usage and estimated cost"
    >
      <div className="api-usage-head">
        <div>
          <strong>{compact ? "OpenAI usage" : "API usage & estimated cost"}</strong>
          {filename && <div className="api-usage-file">{fileLabel}: {filename}</div>}
        </div>
        <span className="badge accent mono">{model}</span>
      </div>

      <dl className="api-usage-grid">
        <UsageMetric label="Requests" value={formatTokenCount(usage.request_count)} />
        <UsageMetric label="Input tokens" value={formatTokenCount(usage.input_tokens)} />
        <UsageMetric
          label="Cached input"
          value={formatTokenCount(usage.cached_input_tokens)}
          hint="Included in input"
        />
        <UsageMetric
          label="Output tokens"
          value={formatTokenCount(usage.output_tokens)}
          hint={usage.reasoning_tokens > 0
            ? `${formatTokenCount(usage.reasoning_tokens)} reasoning included`
            : undefined}
        />
        <UsageMetric label="Total tokens" value={formatTokenCount(usage.total_tokens)} />
        <UsageMetric
          label="Estimated OpenAI cost"
          value={formatEstimatedCost(usage.estimated_cost_usd)}
          emphasized={costAvailable}
        />
      </dl>

      {!compact && (
        <div className="api-usage-note">
          {costAvailable
            ? "Estimate uses standard text-token rates. Cached input is already included in input tokens; non-OpenAI services and custom or regional pricing are excluded."
            : "Token counts are available, but a cost estimate is unavailable because pricing is not configured for every model used."}
        </div>
      )}
    </section>
  );
}

function UsageMetric({
  label,
  value,
  hint,
  emphasized = false,
}: {
  label: string;
  value: string;
  hint?: string;
  emphasized?: boolean;
}) {
  return (
    <div className={`api-usage-metric${emphasized ? " api-usage-cost" : ""}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
      {hint && <small>{hint}</small>}
    </div>
  );
}
