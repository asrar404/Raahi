module.exports = function (api) {
  api.cache(true);
  return {
    // babel-preset-expo covers React Native, JSX and the EXPO_PUBLIC_*
    // environment inlining that constants/config.ts relies on.
    //
    // No reanimated plugin here: nothing in this app uses Reanimated
    // (animations are plain Animated, and bottom-tabs v6 does not require it).
    // Listing a plugin that is not installed fails the Metro build outright.
    presets: ['babel-preset-expo'],
  };
};
