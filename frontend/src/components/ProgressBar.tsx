/**
 * ProgressBar – animated horizontal progress indicator (RTL-aware).
 */

interface Props {
  pct: number;   // 0–100
  stage: string; // Hebrew stage label
}

export function ProgressBar({ pct, stage }: Props) {
  return (
    <div className="w-full">
      <div className="flex justify-between mb-1 text-sm text-gray-600">
        <span>{stage}</span>
        <span dir="ltr">{pct}%</span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2.5">
        <div
          className="bg-blue-600 h-2.5 rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
