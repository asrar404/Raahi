/**
 * App root.
 *
 * `GestureHandlerRootView` must wrap everything and must have flex: 1 —
 * react-native-screens and the bottom-tab navigator both depend on it, and
 * without it gestures silently stop working on Android.
 */

import React, { useEffect } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { StyleSheet } from 'react-native';

import AppNavigator from './navigation/AppNavigator';
import { initAuthListener } from './services/supabase';

export default function App() {
  // Keeps the access token in the auth store fresh. Without it, requests start
  // failing with 401 about an hour into a session.
  useEffect(() => initAuthListener(), []);

  return (
    <GestureHandlerRootView style={styles.root}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <AppNavigator />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
