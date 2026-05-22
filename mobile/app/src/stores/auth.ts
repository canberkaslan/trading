import { create } from 'zustand';

interface AuthState {
  userId: string | null;
  email: string | null;
  isAuthenticated: boolean;
  signIn: (userId: string, email: string) => void;
  signOut: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  userId: null,
  email: null,
  isAuthenticated: false,
  signIn: (userId, email) => set({ userId, email, isAuthenticated: true }),
  signOut: () => set({ userId: null, email: null, isAuthenticated: false }),
}));
