import { create } from 'zustand'

type AuthState = {
  authenticated: boolean
  sessionChecked: boolean
  username: string | null
  setSession: (authenticated: boolean, username?: string | null) => void
  clearSession: () => void
}

/**
 * Stores only the server-confirmed session state.
 * Passwords and signed session cookies stay outside browser storage.
 */
export const useAuthStore = create<AuthState>((set) => ({
  authenticated: false,
  sessionChecked: false,
  username: null,
  setSession: (authenticated, username = null) =>
    set({
      authenticated,
      sessionChecked: true,
      username: authenticated ? username : null,
    }),
  clearSession: () => set({ authenticated: false, sessionChecked: true, username: null }),
}))
