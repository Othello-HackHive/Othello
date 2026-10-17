import type { CloudEvent } from "../lib/cloudApi";

type Point = NonNullable<CloudEvent["metrics"]>;
const parts: Array<{ key: keyof Point; label: string; factor: number; color: string }> = [
  { key: "L", label: "Cable", factor: 1, color: "#54c4ed" },
  { key: "X", label: "Crossings", factor: 5, color: "#ff6558" },
  { key: "O", label: "Overlap", factor: 1, color: "#f5b442" },
  { key: "B", label: "Bends", factor: 0.5, color: "#8bd7ad" },
];

export default function CostHistory({ events }: { events: CloudEvent[] }) {
  const points = events.filter((event): event is CloudEvent & { metrics: Point } =>
    event.stage === "candidate" && !!event.metrics && ["L", "X", "O", "B", "J"].every((key) =>
      Number.isFinite(event.metrics?.[key as keyof Point])),
  );
  const unique = points.filter((point, index) => index === 0 || Math.abs(point.metrics.J - points[index - 1].metrics.J) > 0.005);
  const baseline = unique[0]?.metrics;
  const latest = unique.at(-1)?.metrics;
  const weights = points[0]?.weights;
  const max = Math.max(...unique.map((point) => point.metrics.J), 1);
  const min = Math.min(...unique.map((point) => point.metrics.J), max);
  const plot = unique.map((point, index) => {
    const x = unique.length === 1 ? 0 : index * 100 / (unique.length - 1);
    const y = max === min ? 50 : 88 - (point.metrics.J - min) * 70 / (max - min);
    return `${x},${y}`;
  }).join(" ");

  const bar = (name: string, metrics: Point) => (
    <div className="history-bar-row" key={name}>
      <span>{name}</span>
      <div className="history-bar" aria-label={`${name} cost J ${metrics.J.toFixed(2)}`}>
        {parts.map((part) => (
          <i key={part.key} title={`${part.label}: ${metrics[part.key]} × ${weights?.[part.key as keyof NonNullable<CloudEvent["weights"]>] ?? part.factor}`} style={{
            width: `${Math.max(0, metrics[part.key] * (weights?.[part.key as keyof NonNullable<CloudEvent["weights"]>] ?? part.factor) / max * 100)}%`,
            background: part.color,
          }} />
        ))}
      </div>
      <strong>{metrics.J.toFixed(2)}</strong>
    </div>
  );

  return (
    <section className="cost-history" aria-label="Measured solver cost history">
      <div className="cost-history-head"><span>VERIFIED COST HISTORY</span><strong>{latest ? `J ${latest.J.toFixed(2)}` : "WAITING FOR A CANDIDATE"}</strong></div>
      {latest ? <>
        <svg className="history-sparkline" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={`Verified cost changed from ${baseline?.J.toFixed(2)} to ${latest.J.toFixed(2)}`}>
          <polyline points={plot} fill="none" stroke="#60d3d2" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          {unique.map((point, index) => <circle key={`${index}-${point.metrics.J}`} cx={unique.length === 1 ? 0 : index * 100 / (unique.length - 1)} cy={max === min ? 50 : 88 - (point.metrics.J - min) * 70 / (max - min)} r="1.5" fill="#d6ffff" />)}
        </svg>
        {baseline && bar("F0", baseline)}
        {bar("LATEST", latest)}
        <div className="history-legend">{parts.map((part) => <span key={part.key}><i style={{ background: part.color }} />{part.key} × {weights?.[part.key as keyof NonNullable<CloudEvent["weights"]>] ?? part.factor}</span>)}</div>
        <small>{unique.length} measured candidate{unique.length === 1 ? "" : "s"} · only checker-verified scores are plotted</small>
      </> : <p>Scores appear when the solver finds a checker-verified design. Search stages alone do not contain cost metrics.</p>}
    </section>
  );
}
