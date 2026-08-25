/**
 * Main tab navigator.
 *
 * The Journey tab holds its own stack (input -> selection -> map) because
 * planning is a linear flow with a back button, while the tabs themselves are
 * lateral navigation.
 *
 * The Map tab points at the same MapView. During a live trip that tab shows a
 * dot, so someone who wandered into Budget or Profile can get back to
 * navigation in one tap.
 */

import React from 'react';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { StyleSheet, Text, View } from 'react-native';

import { colors } from '../constants/colors';
import type { JourneyStackParamList, TabParamList } from '../constants/routes';
import ExpenseLogScreen from '../screens/ExpenseLogScreen';
import IntentInputScreen from '../screens/IntentInputScreen';
import MapViewScreen from '../screens/MapViewScreen';
import ProfileScreen from '../screens/ProfileScreen';
import RouteSelectionScreen from '../screens/RouteSelectionScreen';
import { useSafetyStore } from '../store/safetySlice';
import { useTripStore } from '../store/tripSlice';

const JourneyStack = createNativeStackNavigator<JourneyStackParamList>();
const Tabs = createBottomTabNavigator<TabParamList>();

function JourneyStackNavigator() {
  return (
    <JourneyStack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.text,
        headerTitleStyle: { fontWeight: '800' },
        headerShadowVisible: false,
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <JourneyStack.Screen
        name="IntentInput"
        component={IntentInputScreen}
        options={{ headerShown: false }}
      />
      <JourneyStack.Screen
        name="RouteSelection"
        component={RouteSelectionScreen}
        options={{ title: 'Choose a route' }}
      />
      <JourneyStack.Screen
        name="MapView"
        component={MapViewScreen}
        options={{ headerShown: false }}
      />
    </JourneyStack.Navigator>
  );
}

/**
 * Text-only tab icon.
 *
 * Avoids pulling in an icon font, and a short label reads unambiguously at a
 * glance. The dot marks an active trip or an SOS.
 */
function TabIcon({
  label,
  focused,
  badge,
  danger,
}: {
  label: string;
  focused: boolean;
  badge?: boolean;
  danger?: boolean;
}) {
  const tint = danger ? colors.danger : focused ? colors.accent : colors.muted;
  return (
    <View style={styles.iconWrap}>
      <Text style={[styles.iconText, { color: tint }]}>{label}</Text>
      {badge ? (
        <View style={[styles.badge, { backgroundColor: danger ? colors.danger : colors.accent }]} />
      ) : null}
    </View>
  );
}

export default function TabNavigator() {
  const activeTrip = useTripStore((s) => s.activeTrip);
  const sosActive = useSafetyStore((s) => s.sosActive);
  const pendingReroute = useSafetyStore((s) => s.pendingReroute);
  const tripLive = Boolean(activeTrip);

  return (
    <Tabs.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: styles.tabBar,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted,
        tabBarLabelStyle: styles.tabLabel,
      }}
    >
      <Tabs.Screen
        name="Journey"
        component={JourneyStackNavigator}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon label="Plan" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="Map"
        component={MapViewScreen}
        options={{
          tabBarIcon: ({ focused }) => (
            <TabIcon
              label="Live"
              focused={focused}
              badge={tripLive || Boolean(pendingReroute)}
              danger={sosActive}
            />
          ),
        }}
      />
      <Tabs.Screen
        name="Budget"
        component={ExpenseLogScreen}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon label="Spend" focused={focused} />,
        }}
      />
      <Tabs.Screen
        name="Profile"
        component={ProfileScreen}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon label="You" focused={focused} />,
        }}
      />
    </Tabs.Navigator>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    backgroundColor: colors.surface,
    borderTopColor: colors.border,
    borderTopWidth: 1,
    height: 62,
    paddingBottom: 8,
    paddingTop: 6,
  },
  tabLabel: { fontSize: 10, fontWeight: '600' },
  iconWrap: { alignItems: 'center', justifyContent: 'center', width: 52 },
  iconText: { fontSize: 13, fontWeight: '800' },
  badge: {
    position: 'absolute',
    top: -2,
    right: 6,
    width: 7,
    height: 7,
    borderRadius: 4,
  },
});
