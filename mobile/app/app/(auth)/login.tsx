import { View, Text, StyleSheet, TextInput, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useState } from 'react';
import { useRouter } from 'expo-router';

export default function LoginScreen() {
  const router = useRouter();
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
        placeholderTextColor="#666"
        style={styles.input}
      />
      <TextInput
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        placeholder="password"
        placeholderTextColor="#666"
        style={styles.input}
      />
      <Pressable
        onPress={() => router.replace('/(tabs)/portfolio')}
        style={styles.button}
      >
        <Text style={styles.buttonText}>Sign in</Text>
      </Pressable>
      <Text style={styles.disclaimer}>
        This app is not investment advice. Past performance does not guarantee future
        results.
      </Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0a0a', padding: 24, gap: 16 },
  heading: { color: '#fff', fontSize: 36, fontWeight: '700', marginTop: 32 },
  subheading: { color: '#888', fontSize: 14, marginBottom: 32 },
  input: {
    backgroundColor: '#171717',
    color: '#fff',
    padding: 16,
    borderRadius: 8,
    fontSize: 16,
  },
  button: {
    backgroundColor: '#22c55e',
    padding: 16,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 8,
  },
  buttonText: { color: '#000', fontWeight: '700', fontSize: 16 },
  disclaimer: {
    color: '#555',
    fontSize: 11,
    fontStyle: 'italic',
    marginTop: 32,
  },
});
