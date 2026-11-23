import { createContext, useContext, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { KeyRound, LockKeyhole, LogOut, RefreshCw } from "lucide-react";
import { request } from "../api";
import { message } from "./client";

type Principal = {
  id: string;
  role: string;
  project_ids: string[];
  all_projects: boolean;
  permissions: string[];
};
export type AccessSession = {
  mode: "local" | "managed";
  authenticated: boolean;
  principal: Principal | null;
  deployment?: string;
  workspace_kind?: "organization" | "personal" | "local";
  permissions?: string[];
};
const AccessContext = createContext<AccessSession | null>(null);

export function useAccessSession() {
  return useContext(AccessContext);
}

export function permits(session: AccessSession | null, permission: string) {
  return (
    session?.mode === "local" ||
    !!session?.principal?.permissions?.includes(permission)
  );
}

/** The credential lives only in this form; the server owns the HttpOnly session. */
export function AccessGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AccessSession | null>(null);
  const [error, setError] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    setError("");
    void request<AccessSession>("/access/session")
      .then((value) => {
        if (alive) setSession(value);
      })
      .catch((reason) => {
        if (alive) setError(message(reason));
      });
    return () => {
      alive = false;
    };
  }, [revision]);
  useEffect(() => {
    const refresh = () => {
      setSession(null);
      setRevision((value) => value + 1);
    };
    window.addEventListener("doxa-access-required", refresh);
    return () => window.removeEventListener("doxa-access-required", refresh);
  }, []);
  async function signIn(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const value = await request<AccessSession>("/access/session", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      setToken("");
      setSession(value);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }
  if (session?.authenticated)
    return (
      <AccessContext.Provider value={session}>
        {children}
      </AccessContext.Provider>
    );
  return (
    <main className="w-access-gate">
      <div className="w-access-card">
        <a href="https://github.com/sargonxg/DOXA-by-TACITUS" className="w-access-brand">
          DOXA <span>BY TACITUS</span>
        </a>
        <LockKeyhole size={30} strokeWidth={1.4} />
        <h1>
          {session
            ? "Open your private workspace"
            : "Checking workspace access"}
        </h1>
        <p>
          Company language audits, public-source research and personal writing
          review, with explicit methods and inspectable evidence.
        </p>
        {session && (
          <form onSubmit={(event) => void signIn(event)}>
            <label htmlFor="doxa-access-token">
              Your individual access credential
            </label>
            <input
              id="doxa-access-token"
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              required
              disabled={busy}
              spellCheck={false}
              autoCapitalize="none"
            />
            <button
              className="w-button primary"
              type="submit"
              disabled={busy || !token.trim()}
            >
              <KeyRound size={16} />{" "}
              {busy ? "Checking credential…" : "Open private workspace"}
            </button>
            <small>
              Use the credential supplied by your workspace operator. It is
              exchanged for a private session and is not saved in browser
              storage.
            </small>
          </form>
        )}
        {error && (
          <p className="w-access-error" role="alert">
            {error}
          </p>
        )}
        {!session && !error && (
          <p role="status">Connecting to the workspace…</p>
        )}
        {error && (
          <button
            className="w-button outline"
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw size={14} /> Retry access check
          </button>
        )}
      </div>
    </main>
  );
}

export function AccessControls() {
  const session = useAccessSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!session || session.mode !== "managed") return null;
  return (
    <div className="w-access-controls">
      <span title={session.principal?.id}>
        {session.principal?.role} · private pilot
      </span>
      <button
        aria-label="Sign out of private workspace"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setError("");
          void request("/access/session", { method: "DELETE" })
            .then(() => window.dispatchEvent(new Event("doxa-access-required")))
            .catch((reason) => setError(message(reason)))
            .finally(() => setBusy(false));
        }}
      >
        <LogOut size={14} />
        <span>Sign out</span>
      </button>
      {error && <span role="alert">{error}</span>}
    </div>
  );
}
