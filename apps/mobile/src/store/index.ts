/**
 * Store barrel.
 *
 * Four independent Zustand stores rather than one combined store: auth and
 * budget need AsyncStorage persistence, while trip and safety state must not
 * survive a restart. Splitting them keeps each store's persistence rule
 * explicit and avoids re-rendering the map when an unrelated slice changes.
 */

export { getAuthToken, getCurrentUser, useAuthStore } from './authSlice';
export {
  selectPercentUsed,
  selectRemaining,
  selectSeverity,
  selectUnsynced,
  useBudgetStore,
} from './budgetSlice';
export type { LocalExpense } from './budgetSlice';
export { selectGeoAlerts, useSafetyStore } from './safetySlice';
export { selectCurrentLeg, useTripStore } from './tripSlice';
export type {
  BudgetSummary,
  EmergencyContact,
  ExpenseCategory,
  ExpenseLog,
  LegStatus,
  ParsedIntent,
  PlanResponse,
  PlannedRoute,
  ResolvedPlace,
  RiskZone,
  RouteLeg,
  SafeRefuge,
  SafetyAlert,
  TransitMode,
  Trip,
  TripLeg,
  TripStatus,
  UserProfile,
  WsEvent,
  WsMessage,
} from './types';
