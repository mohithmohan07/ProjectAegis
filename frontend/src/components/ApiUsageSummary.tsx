import type { OpenAIUsage } from "../types";

interface ApiUsageSummaryProps {
  usage?: OpenAIUsage | null;
  compact?: boolean;
  filename?: string;
  fileLabel?: string;
  cumulative?: boolean;
  resumed?: boolean;
}

const TOKEN_FORMATTER = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

export function formatTokenCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? TOKEN_FORMATTER.format(value)
    : "—";
}

export function formatElapsedSeconds(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return "\u2014";
  }
  const total = Math.round(value);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
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
  cumulative = false,
  resumed = false,
}: ApiUsageSummaryProps) {
  const requestCount = finiteNumber(usage?.request_count);
  const totalTokens = finiteNumber(usage?.total_tokens);
  if (!usage || (requestCount <= 0 && totalTokens <= 0)) return null;

  const costAvailable = usage.estimated_cost_usd != null;
  const model = usage.model || "Unknown model";
  const heading = compact
    ? cumulative ? "Cumulative model usage" : "Model usage"
    : cumulative
      ? "Cumulative API usage & estimated cost"
      : "API usage & estimated cost";

  return (
    <section
      className={`api-usage${compact ? " api-usage-compact" : ""}`}
      data-testid="api-usage-summary"
      aria-label="OpenAI API usage and estimated cost"
    >
      <div className="api-usage-head">
        <div>
          <strong>{heading}</strong>
          {filename && <div className="api-usage-file">{fileLabel}: {filename}</div>}
        </div>
        <div className="api-usage-badges">
          {resumed && <span className="badge green">Resumed</span>}
          <span className="badge accent mono">{model}</span>
        </div>
      </div>

      <dl className="api-usage-grid">
        <UsageMetric label="Requests" value={formatTokenCount(requestCount)} />
        <UsageMetric label="Input tokens" value={formatTokenCount(usage.input_tokens)} />
        <UsageMetric
          label="Cached input"
          value={formatTokenCount(usage.cached_input_tokens)}
          hint="Included in input"
        />
        <UsageMetric
          label="Cache writes"
          value={formatTokenCount(usage.cache_write_tokens ?? 0)}
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
          label="Estimated cost"
          value={formatEstimatedCost(usage.estimated_cost_usd)}
          emphasized={costAvailable}
        />
        {typeof usage.elapsed_seconds === "number" && usage.elapsed_seconds > 0 && (
          <UsageMetric
            label="Time taken"
            value={formatElapsedSeconds(usage.elapsed_seconds)}
            hint={cumulative ? "Across parsing and every attempt" : undefined}
          />
        )}
      </dl>

      {!compact && (usage.stages?.length ?? 0) > 0 && (
        <div className="api-usage-stages">
          <table>
            <thead>
              <tr>
                <th scope="col">Stage</th>
                <th scope="col">Requests</th>
                <th scope="col">Tokens</th>
                <th scope="col">Est. cost</th>
                <th scope="col">Time</th>
              </tr>
            </thead>
            <tbody>
              {(usage.stages ?? []).map((row, index) => (
                <tr key={`${row.stage}\u0000${row.lane}\u0000${index}`}>
                  <td>
                    {row.stage || "(unattributed)"}
                    {row.lane ? <small> {row.lane}</small> : null}
                  </td>
                  <td>{formatTokenCount(row.request_count)}</td>
                  <td>{formatTokenCount(row.total_tokens)}</td>
                  <td>
                    {row.pricing_complete
                      ? formatEstimatedCost(row.estimated_cost_usd)
                      : "\u2014"}
                  </td>
                  <td>{formatElapsedSeconds(row.elapsed_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!compact && (
        <div className="api-usage-note">
          {cumulative
            ? (
              <>
                {resumed
                  ? "Resumed from a saved checkpoint. "
                  : ""}
                Totals are cumulative for this file across parsing, the
                original attempt, and every retry; retrying does not reset
                them.{" "}
                {costAvailable
                  ? "The estimate uses the active model's published rates, including cache-write charges and long-context multipliers where they apply; cached input and cache writes are already included in input tokens."
                  : "A cost estimate is unavailable because pricing is not configured for every model used."}
              </>
            )
            : costAvailable
              ? "Estimate uses the active model's published rates, including cache-write charges and long-context multipliers where they apply. Cached input and cache writes are already included in input tokens; custom or regional pricing is excluded."
              : "Token counts are available, but a cost estimate is unavailable because pricing is not configured for every model used."}
        </div>
      )}
    </section>
  );
}

function finiteNumber(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
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
