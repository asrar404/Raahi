/**
 * Auth stack.
 *
 * A single screen today, but kept as its own navigator so onboarding,
 * OTP-as-a-screen or a terms step can be added without restructuring the root.
 */

import React from 'react';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import { colors } from '../constants/colors';
import AuthScreen from '../screens/AuthScreen';

const Stack = createNativeStackNavigator();

export default function AuthNavigator() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Screen name="SignIn" component={AuthScreen} />
    </Stack.Navigator>
  );
}
