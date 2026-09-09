import type { OpenAIUsage, ProviderRequestUsage } from "../types";
import { finiteUsageNumber, providerRequestCount, usageCost, usageCostInr, usageCostInrNotes } from "../lib/apiUsage";

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

export function formatEstimatedCost(value: number | null | undefined, currency: "USD" | "INR" = "USD"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Unavailable";
  const digits = Math.abs(value) < 0.01 ? 6
    : currency === "INR" && Math.abs(value) >= 1 ? 2 : 4;
  return new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", {
    style: "currency",
    currency,
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
  if (!usage) return null;
  const requestCount = providerRequestCount(usage);
  const totalTokens = finiteUsageNumber(usage.total_tokens);
  const attemptCount = finiteUsageNumber(usage.attempt_count);
  const elapsedSeconds = finiteUsageNumber(
    usage.active_elapsed_seconds ?? usage.elapsed_seconds,
  );
  const reviewWaitSeconds = finiteUsageNumber(usage.review_wait_seconds);
  const wallElapsedSeconds = finiteUsageNumber(
    usage.wall_elapsed_seconds,
  );
  const processingSeconds = finiteUsageNumber(usage.mechanical_wall_seconds);
  if (requestCount <= 0 && totalTokens <= 0 && attemptCount <= 0
    && elapsedSeconds <= 0 && reviewWaitSeconds <= 0
    && wallElapsedSeconds <= 0 && processingSeconds <= 0
    && finiteUsageNumber(usage.mechanical_span_count) <= 0) return null;

  const cost = usageCostInr(usage);
  const dollars = usageCost(usage);
  const notes = usageCostInrNotes(usage);
  const latestRequest = usage.latest_request;
  const costAvailable = cost.value !== null;
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
      aria-label="API usage and estimated cost"
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
        <UsageMetric
          label="Requests"
          value={formatTokenCount(requestCount)}
          hint={usage.attempt_coverage_complete === false
            ? "Complete request history unavailable"
            : cost.usageGap ? "Includes requests with missing usage"
            : cost.pendingCount > 0 ? "Includes requests still running" : undefined}
        />
        {attemptCount > requestCount && (
          <UsageMetric label="Attempts" value={formatTokenCount(attemptCount)}
            hint="Includes attempts that did not reach the provider" />
        )}
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
          label={cost.recordedOnly && costAvailable ? "Recorded estimate" : "Estimated cost"}
          value={formatEstimatedCost(cost.value, "INR")}
          hint={dollars.value !== null
            ? `${formatEstimatedCost(dollars.value)} USD${dollars.recordedOnly ? " recorded" : ""}`
            : cost.recordedOnly && costAvailable ? "Reported, priced usage only" : undefined}
          emphasized={costAvailable}
        />
        {latestRequest && <UsageMetric
          label="Latest request"
          value={formatEstimatedCost(latestRequest.estimated_cost_inr, "INR")}
          hint={`${latestRequest.actual_model || latestRequest.model || latestRequest.requested_model || "Unknown model"}${latestRequest.stage ? ` · ${latestRequest.stage}` : ""}${latestRequest.estimated_cost_inr == null && latestRequest.estimated_cost_usd != null ? ` · ${formatEstimatedCost(latestRequest.estimated_cost_usd)} USD` : ""}`}
        />}
        {elapsedSeconds > 0 && (
          <UsageMetric
            label="Time taken"
            value={formatElapsedSeconds(elapsedSeconds)}
            hint={cumulative
              ? "Active processing across parsing and every attempt"
              : "Active processing time"}
          />
        )}
        {reviewWaitSeconds > 0 && (
          <UsageMetric
            label="Review waiting"
            value={formatElapsedSeconds(reviewWaitSeconds)}
            hint="Paused human review time; excluded from processing"
          />
        )}
        {wallElapsedSeconds > 0 && (
          <UsageMetric
            label="Total wall time"
            value={formatElapsedSeconds(wallElapsedSeconds)}
            hint="From the first Concept stage through the latest state"
          />
        )}
        {processingSeconds > 0 && (
          <UsageMetric label="Local processing"
            value={formatElapsedSeconds(processingSeconds)}
            hint={elapsedSeconds > 0 ? "Included in total time" : undefined} />
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
                <th scope="col">Est. cost (INR)</th>
                <th scope="col">Time</th>
              </tr>
            </thead>
            <tbody>
              {(usage.stages ?? []).map((row, index) => {
                const rowCost = usageCostInr(row);
                const rowDollars = usageCost(row);
                return (
                  <tr key={`${row.stage}\u0000${row.lane}\u0000${index}`}>
                    <td>
                      {row.stage || "(unattributed)"}
                      {row.lane ? <small> {row.lane}</small> : null}
                    </td>
                    <td>{formatTokenCount(providerRequestCount(row))}</td>
                    <td>{formatTokenCount(row.total_tokens)}</td>
                    <td title={usageCostInrNotes(row).join(" ")}>
                      {formatEstimatedCost(rowCost.value, "INR")}
                      {rowCost.recordedOnly && rowCost.value !== null && <small> recorded</small>}
                      {rowCost.value === null && rowDollars.value !== null && <small> · {formatEstimatedCost(rowDollars.value)} USD</small>}
                      {rowCost.pendingCount > 0 && <small> · {rowCost.pendingCount} pending</small>}
                      {rowCost.usageGap && <small> · Usage incomplete</small>}
                      {rowCost.pricingMissing && <small> · Pricing incomplete</small>}
                      {rowCost.conversionMissing && <small> · INR conversion incomplete</small>}
                    </td>
                    <td>{formatElapsedSeconds(row.elapsed_seconds)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!compact && (usage.request_attempts?.length ?? 0) > 0 && (
        <div className="api-usage-stages">
          <table aria-label="Individual provider request costs">
            <thead><tr>
              <th scope="col">Model</th><th scope="col">Stage</th>
              <th scope="col">Status</th><th scope="col">Tokens</th>
              <th scope="col">Est. cost (INR)</th>
            </tr></thead>
            <tbody>{usage.request_attempts!.map((request) => (
              <RequestCostRow key={request.attempt_id} request={request} />
            ))}</tbody>
          </table>
        </div>
      )}

      {notes.map((note) => <div className="api-usage-note" key={note}>{note}</div>)}
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
                {cost.usageGap
                  ? "Reported usage is retained; missing usage is not counted as free."
                  : costAvailable
                  ? "The estimate uses the active model's published rates, including cache-write charges and long-context multipliers where they apply; cached input and cache writes are already included in input tokens."
                  : "Reported token counts are retained."}
              </>
            )
            : cost.usageGap
              ? "Reported usage is retained; missing usage is not counted as free."
              : costAvailable
              ? "Estimate uses the active model's published rates, including cache-write charges and long-context multipliers where they apply. Cached input and cache writes are already included in input tokens; custom or regional pricing is excluded."
              : "Reported token counts are retained."}
        </div>
      )}
    </section>
  );
}

function RequestCostRow({ request }: { request: ProviderRequestUsage }) {
  const outcome = request.outcome || request.status;
  const status = outcome === "queued" ? "Queued"
    : outcome === "in_flight" ? "Running"
    : outcome === "error" ? "Failed"
    : request.usage_reported ? "Reported" : "Usage unavailable";
  return <tr>
    <td>{request.actual_model || request.model || request.requested_model || "Unknown model"}</td>
    <td>{request.stage || "(unattributed)"}{request.lane ? ` · ${request.lane}` : ""}</td>
    <td>{status}</td>
    <td>{formatTokenCount(request.total_tokens)}</td>
    <td title={request.usd_to_inr_as_of ? `Exchange rate recorded ${request.usd_to_inr_as_of}` : undefined}>
      {formatEstimatedCost(request.estimated_cost_inr, "INR")}
      {request.estimated_cost_inr == null && request.estimated_cost_usd != null
        && <small> · {formatEstimatedCost(request.estimated_cost_usd)} USD</small>}
    </td>
  </tr>;
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
