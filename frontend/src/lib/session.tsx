import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { Me } from "./types";
import { api, ApiError } from "./api";

interface SessionState {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionState>({
  me: null,
  loading: true,
  refresh: async () => {},
  logout: async () => {},
});

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setMe(await api.get<Me>("/api/me"));
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) console.error(e);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      window.location.href = "/login";
    }
  }, []);

  useEffect(() => {
    // /login renders without a session; don't bounce through the 401 redirect
    if (window.location.pathname === "/login") {
      setLoading(false);
      return;
    }
    void refresh();
  }, [refresh]);

  return (
    <SessionContext.Provider value={{ me, loading, refresh, logout }}>
      {children}
    </SessionContext.Provider>
  );
}

export const useSession = () => useContext(SessionContext);
