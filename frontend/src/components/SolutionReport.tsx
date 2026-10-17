import type { PlanModel, Vec2 } from "../lib/resplan";
import type { Solution } from "../lib/optimizer";
import type { CloudEvent } from "../lib/cloudApi";

function PlanDrawing({ plan, solution }: { plan: PlanModel; solution: Solution }) {
  const pad = Math.max(plan.bounds.size[0], plan.bounds.size[1]) * 0.07;
  const coords = (points: Vec2[]) => points.map(([x, y]) => `${x},${y}`).join(" ");
  return <svg className="report-plan" viewBox={`${plan.bounds.min[0] - pad} ${plan.bounds.min[1] - pad} ${plan.bounds.size[0] + pad * 2} ${plan.bounds.size[1] + pad * 2}`} role="img" aria-label="Floor plan and routed hub overlay">
    <rect x={plan.bounds.min[0] - pad} y={plan.bounds.min[1] - pad} width={plan.bounds.size[0] + pad * 2} height={plan.bounds.size[1] + pad * 2} fill="#eef4f7" />
    {plan.rooms.map((room) => <polygon key={room.id} points={coords(room.polygon)} fill={room.eligible ? "#fff" : "#dce6eb"} stroke="#607887" strokeWidth="0.045" />)}
    {solution.hubs.flatMap((hub) => hub.route_segments.map((segment, index) => <polyline key={`${hub.hub_id}-${index}`} points={coords(segment.polyline)} fill="none" stroke={hub.color} strokeWidth="0.14" strokeLinecap="round" strokeLinejoin="round" />))}
    {solution.hubs.map((hub) => <g key={hub.hub_id}><circle cx={hub.location[0]} cy={hub.location[1]} r="0.25" fill={hub.color} stroke="white" strokeWidth="0.06" /><text x={hub.location[0] + 0.3} y={hub.location[1]} fontSize="0.55" fill="#152632" fontWeight="bold">{hub.hub_id}</text></g>)}
  </svg>;
}

export default function SolutionReport({ plan, solution, source, weights }: {
  plan: PlanModel;
  solution: Solution;
  source: string;
  weights?: CloudEvent["weights"];
}) {
  const metrics = solution.metrics;
  return <article className="print-report">
    <header className="report-header"><div><p>NodeRithm / SOLUTION REPORT</p><h1>{plan.plan_id}</h1><span>{source}</span></div><strong>J {metrics.J.toFixed(2)}</strong></header>
    <section className="report-summary"><div><span>HUBS</span><strong>{solution.hubs.length}</strong></div><div><span>CABLE</span><strong>{metrics.L.toFixed(2)} m</strong></div><div><span>CHECKS</span><strong>{solution.checks.filter((check) => !check.na && check.pass).length}/{solution.checks.filter((check) => !check.na).length}</strong></div></section>
    <h2>Floor plan and route overlay</h2>
    <PlanDrawing plan={plan} solution={solution} />
    <div className="report-columns">
      <section><h2>Hub locations and rings</h2><table><thead><tr><th>Hub</th><th>Room</th><th>Location (m)</th><th>Ring (m)</th></tr></thead><tbody>{solution.hubs.map((hub) => <tr key={hub.hub_id}><td>{hub.hub_id}</td><td>{hub.room_id}</td><td>{hub.location.map((n) => n.toFixed(2)).join(", ")}</td><td>{hub.ring_length_m.toFixed(2)}</td></tr>)}</tbody></table></section>
      <section><h2>Score breakdown</h2><table><tbody><tr><th>L · cable</th><td>{metrics.L.toFixed(2)} m × {weights?.L ?? 1}</td></tr><tr><th>X · crossings</th><td>{metrics.X} × {weights?.X ?? 5}</td></tr><tr><th>O · overlap</th><td>{metrics.O.toFixed(2)} m × {weights?.O ?? 1}</td></tr><tr><th>B · bends</th><td>{metrics.B} × {weights?.B ?? 0.5}</td></tr><tr><th>Total J</th><td>{metrics.J.toFixed(2)}</td></tr></tbody></table></section>
    </div>
    <section><h2>Rule checks</h2><table><thead><tr><th>Rule</th><th>Result</th><th>Measured</th></tr></thead><tbody>{solution.checks.map((check) => <tr key={check.id}><td>{check.label}</td><td>{check.na ? "Unknown" : check.pass ? "Pass" : "Fail"}</td><td>{check.value}</td></tr>)}</tbody></table></section>
    <footer>Generated locally from the displayed {source.toLowerCase()} · {new Date().toLocaleDateString()}</footer>
  </article>;
}
