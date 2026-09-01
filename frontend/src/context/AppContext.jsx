import { createContext, useContext, useState, useCallback } from "react";

const AppContext = createContext(null);

const ROLES = ["executive", "vp", "manager", "ic", "revops_admin"];

export function AppProvider({ children }) {
  const [company, setCompany] = useState("techo-solutions");
  const [role, setRole] = useState("executive");
  const [period, setPeriod] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <AppContext.Provider
      value={{
        company,
        setCompany,
        role,
        setRole,
        period,
        setPeriod,
        refreshKey,
        refresh,
        roles: ROLES,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be inside AppProvider");
  return ctx;
}

export default AppContext;
