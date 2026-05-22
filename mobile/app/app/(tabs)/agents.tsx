import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

const AGENTS = [
  { id: 'market', name: 'Market Analyst', desc: 'Technical indicators, chart patterns' },
  { id: 'fundamentals', name: 'Fundamentals Analyst', desc: 'Financial statements, valuation' },
  { id: 'news', name: 'News Analyst', desc: 'Reuters, Bloomberg, earnings releases' },
  { id: 'sentiment', name: 'Sentiment Analyst', desc: 'Reddit, X, social momentum' },
  { id: 'bull', name: 'Bull Researcher', desc: 'Why to buy' },
  { id: 'bear', name: 'Bear Researcher', desc: 'Why to sell' },
  { id: 'pm', name: 'Portfolio Manager', desc: 'Final 5-tier rating' },
] as const;

export default function AgentsScreen() {
  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.heading}>Agent Council</Text>
        <Text style={styles.subheading}>
          7 LLM agents debate every decision — tap any card to see its reasoning.
        </Text>
        {AGENTS.map((agent) => (
          <View key={agent.id} style={styles.card}>
            <Text style={styles.cardName}>{agent.name}</Text>
            <Text style={styles.cardDesc}>{agent.desc}</Text>
            <Text style={styles.cardStatus}>Awaiting first decision…</Text>
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0a0a' },
  scroll: { padding: 24, gap: 12 },
  heading: { color: '#fff', fontSize: 28, fontWeight: '700' },
  subheading: { color: '#888', fontSize: 14, marginBottom: 16 },
  card: { padding: 16, backgroundColor: '#171717', borderRadius: 12 },
  cardName: { color: '#fff', fontSize: 16, fontWeight: '600' },
  cardDesc: { color: '#aaa', fontSize: 13, marginTop: 4 },
  cardStatus: { color: '#666', fontSize: 11, marginTop: 8, fontStyle: 'italic' },
});
