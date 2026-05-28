import { StatChip } from './StatChip';

/** @deprecated use StatChip with sub prop */
export function StatBlock({
  label,
  value,
  accent,
  sub,
}: {
  label: string;
  value: string;
  accent?: string;
  sub?: string;
}) {
  return <StatChip label={label} value={value} color={accent} sub={sub} compact />;
}
