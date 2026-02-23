/**
 * ConfidenceBadge – shows a coloured pill with the confidence percentage.
 * High ≥ 85%, Medium 60-84%, Low < 60%.
 */

interface Props {
  value: number; // 0–1
}

export function ConfidenceBadge({ value }: Props) {
  const pct = Math.round(value * 100);
  let cls = "bg-green-100 text-green-800";
  if (pct < 60) cls = "bg-red-100 text-red-800";
  else if (pct < 85) cls = "bg-yellow-100 text-yellow-800";

  return (
    <span className={`inline-block text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>
      {pct}%
    </span>
  );
}
