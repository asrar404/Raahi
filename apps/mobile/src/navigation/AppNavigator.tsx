/**
 * Root navigator.
 *
 * Switches between the auth flow and the main tabs based on whether a profile
 * exists. Rendering is held back until the persisted auth store has hydrated
 * and any in-progress trip has been restored — otherwise a returning user sees
 * the sign-in screen flash on every cold start.
 */

import React from 'react';
import { NavigationContainer, type Theme } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { colors } from '../constants/colors';
import type { RootStackParamList } from '../constants/routes';
import { useRestoreActiveTrip } from '../hooks/useTrip';
import { useAuthStore } from '../store/authSlice';
import AuthNavigator from './AuthNavigator';
import TabNavigator from './TabNavigator';

const Stack = createNativeStackNavigator<RootStackParamList>();

const navTheme: Theme = {
  dark: true,
  colors: {
    primary: colors.accent,
    background: colors.bg,
    card: colors.surface,
    text: colors.text,
    border: colors.border,
    notification: colors.danger,
  },
};

function SplashScreen({ message }: { message: string }) {
  return (
    <View style={styles.splash}>
      <Text style={styles.logo}>RAAHI</Text>
      <ActivityIndicator color={colors.accent} style={styles.spinner} />
      <Text style={styles.message}>{message}</Text>
    </View>
  );
}

export default function AppNavigator() {
  const hydrated = useAuthStore((s) => s.hydrated);
  const user = useAuthStore((s) => s.user);

  const signedIn = Boolean(user);
  // Only attempt a restore once we know who the user is.
  const { restoring } = useRestoreActiveTrip(hydrated && signedIn);

  if (!hydrated) {
    return <SplashScreen message="Loading your profile…" />;
  }
  if (signedIn && restoring) {
    return <SplashScreen message="Checking for an active trip…" />;
  }

  return (
    <NavigationContainer theme={navTheme}>
      <Stack.Navigator
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.bg },
          animation: 'fade',
        }}
      >
        {signedIn ? (
          <Stack.Screen name="Tabs" component={TabNavigator} />
        ) : (
          <Stack.Screen name="Auth" component={AuthNavigator} />
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
  },
  logo: {
    color: colors.accent,
    fontSize: 40,
    fontWeight: '900',
    letterSpacing: 5,
  },
  spinner: { marginTop: 28 },
  message: { color: colors.muted, fontSize: 13, marginTop: 14 },
});
