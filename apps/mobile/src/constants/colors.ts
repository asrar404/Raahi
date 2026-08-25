/**
 * Dark theme tokens.
 *
 * The palette is deliberately dark: RAAHI is used at night, often outdoors,
 * often one-handed. A bright screen destroys night vision and makes the phone
 * conspicuous, neither of which helps someone walking alone.
 *
 * Risk colours run green -> red across the 1-5 scale used by safety_zones.
 */
export const colors = {
  bg: '#0D0D1A',
  surface: '#1A1A2E',
  surfaceAlt: '#20203A',
  border: '#2A2A3E',

  text: '#EAEAFF',
  textDim: '#A0A0C0',
  muted: '#6B6B8A',

  accent: '#6C63FF',
  accentDim: '#4A43C7',

  success: '#4CAF50',
  warning: '#FF9800',
  danger: '#FF3B30',
  dangerDark: '#8B0000',
  info: '#38BDF8',
} as const;

/** Risk score (1 = safest, 5 = most dangerous) to colour. */
export const riskColors: Record<number, string> = {
  1: '#4CAF50',
  2: '#8BC34A',
  3: '#FF9800',
  4: '#FF5722',
  5: '#FF3B30',
};

/**
 * Safety score (0-5, where 5 is safest) to colour.
 * Note this runs the opposite way to riskColors — safety scores come from
 * fn_point_safety_score, risk scores from safety_zones.risk_score.
 */
export function safetyColor(score: number): string {
  if (score >= 4) return colors.success;
  if (score >= 3) return '#8BC34A';
  if (score >= 2) return colors.warning;
  return colors.danger;
}

export function riskColor(score: number): string {
  return riskColors[Math.max(1, Math.min(5, Math.round(score)))] ?? colors.muted;
}

/** Per-mode accent, so route cards are scannable at a glance. */
export const modeColors: Record<string, string> = {
  walk: '#8BC34A',
  metro: '#6C63FF',
  bus: '#38BDF8',
  train: '#00BCD4',
  auto: '#FFC107',
  cab: '#FF9800',
  rapido: '#FF5722',
  ferry: '#03A9F4',
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
} as const;

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  pill: 999,
} as const;
