import { Text, StyleSheet, TextInput, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useMemo, useState } from 'react';
import { useRouter } from 'expo-router';
import { useTranslation } from 'react-i18next';

import { useTheme } from '@/theme/useTheme';
import { MIN_TOUCH_TARGET } from '@/utils/a11y';

export default function LoginScreen() {
  const router = useRouter();
  const { t } = useTranslation();
  const theme = useTheme();
  const styles = useMemo(() => makeStyles(theme), [theme]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  return (
    <SafeAreaView style={styles.container}>
      <Text style={styles.heading}>Trading</Text>
      <Text style={styles.subheading}>AI-powered, agent-driven</Text>
      <TextInput
        value={email}
        onChangeText={setEmail}
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        placeholder="email"
        placeholderTextColor={theme.textSecondary}
        style={styles.input}
      />
      <TextInput
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        placeholder="password"
        placeholderTextColor={theme.textSecondary}
        style={styles.input}
      />
      <Pressable
        onPress={() => router.replace('/(tabs)/portfolio')}
        style={styles.button}
        accessibilityRole="button"
        accessibilityLabel="Sign in"
      >
        <Text style={styles.buttonText}>Sign in</Text>
      </Pressable>
      {/* Was an English string hard-coded into this one screen while every
          other surface reads it from the bundle. */}
      <Text style={styles.disclaimer}>{t('disclaimer.short')}</Text>
    </SafeAreaView>
  );
}

type Palette = ReturnType<typeof useTheme>;

const makeStyles = (t: Palette) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background, paddingHorizontal: 24, gap: 12 },
    heading: { color: t.textPrimary, fontSize: 40, fontWeight: '800', marginTop: 48, letterSpacing: -1 },
    subheading: { color: t.textSecondary, fontSize: 14, marginBottom: 32 },
    input: {
      backgroundColor: t.surfaceElevated,
      color: t.textPrimary,
      borderWidth: 1,
      borderColor: t.textPrimary,
      paddingHorizontal: 14,
      paddingVertical: 14,
      fontSize: 16,
    },
    button: {
      backgroundColor: t.textPrimary,
      paddingVertical: 15,
      minHeight: MIN_TOUCH_TARGET,
      alignItems: 'center',
      justifyContent: 'center',
      marginTop: 8,
    },
    buttonText: { color: t.background, fontWeight: '800', fontSize: 16, letterSpacing: 0.5 },
    disclaimer: { color: t.textSecondary, fontSize: 11, marginTop: 32, lineHeight: 16 },
  });
