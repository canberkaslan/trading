// ESLint 9 flat config.
//
// Base is `eslint-config-expo/flat` — the right rule set for an Expo +
// React Native + TypeScript app. It brings @typescript-eslint, eslint-plugin-
// react, react-hooks, import and expo, already wired up with the RN globals
// and module-resolution settings this project needs.
//
// Rule choices here are deliberately conservative. This app has ~55 source
// files that have never been linted, so the goal is to catch real defects
// without forcing a large mechanical refactor. The Expo base is already
// mostly `warn`; the overrides below only tighten the few things worth
// failing CI over, and relax the ones whose fixes would be behavioural.

const expoConfig = require('eslint-config-expo/flat');

module.exports = [
  {
    // Build output, native projects and generated dirs. `node_modules` is
    // ignored by ESLint out of the box but is listed for clarity.
    ignores: [
      'node_modules/**',
      '.expo/**',
      'dist/**',
      'build/**',
      'android/**',
      'ios/**',
      'coverage/**',
      'expo-env.d.ts',
    ],
  },

  ...expoConfig,

  {
    files: ['**/*.ts', '**/*.tsx'],
    rules: {
      // Unused code is the main thing a first lint pass should surface, and
      // it is safe to fix. `argsIgnorePattern: '^_'` preserves the intent of
      // the ESLint 8 .eslintrc.js this config replaces, and matches the
      // `_`-prefix convention tsconfig's noUnusedParameters already allows.
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          vars: 'all',
          args: 'after-used',
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          ignoreRestSiblings: true,
          caughtErrors: 'all',
          caughtErrorsIgnorePattern: '^_',
        },
      ],

      // Genuine bug classes, cheap to keep clean.
      'no-var': 'error',
      'prefer-const': 'error',
      eqeqeq: ['error', 'smart'],

      // The default forbid list includes `'` and `"`, which are ordinary
      // punctuation in UI copy — the Turkish strings in this app use the
      // apostrophe as a grammatical suffix separator (Broker'a, Live'a), and
      // rewriting those as &apos; would mangle readable, translatable copy for
      // no safety gain: React Native renders Text children as plain strings,
      // so there is no HTML parsing ambiguity to protect against. `>` and `}`
      // are kept — unescaped in JSX text those almost always mean a typo.
      'react/no-unescaped-entities': ['error', { forbid: ['>', '}'] }],

      // Calling a hook conditionally is always a bug — worth gating on.
      'react-hooks/rules-of-hooks': 'error',

      // Dependency-array fixes change *when* effects re-run, so on a codebase
      // this rule has never been applied to they are behavioural changes, not
      // cleanups. Kept visible as a warning rather than silenced or forced.
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
];
