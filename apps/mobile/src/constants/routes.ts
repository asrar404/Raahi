/**
 * Navigation route names and their parameter types.
 *
 * Centralised so `navigation.navigate()` calls are type-checked instead of
 * relying on string literals scattered across screens.
 */

export const ROUTES = {
  // Auth stack
  AUTH: 'Auth',
  // Main tabs
  TABS: 'Tabs',
  JOURNEY: 'Journey',
  MAP: 'Map',
  BUDGET: 'Budget',
  PROFILE: 'Profile',
  // Pushed screens
  INTENT_INPUT: 'IntentInput',
  ROUTE_SELECTION: 'RouteSelection',
  MAP_VIEW: 'MapView',
  EXPENSE_LOG: 'ExpenseLog',
} as const;

export type RootStackParamList = {
  Auth: undefined;
  Tabs: undefined;
};

export type JourneyStackParamList = {
  IntentInput: undefined;
  RouteSelection: undefined;
  MapView: { tripId?: string } | undefined;
};

export type TabParamList = {
  Journey: undefined;
  Map: undefined;
  Budget: undefined;
  Profile: undefined;
};
