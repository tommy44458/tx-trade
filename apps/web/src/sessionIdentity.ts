// The local app has one internal workspace owner (for example "local-demo")
// that is not a person and is never shown. A remote session signed in with an
// account (such as Google) reports who is connected, and the sidebar shows it.
// A local workspace signed in to the txinTrade cloud shows that account too.
type CloudProfile = { email?: string | null; display_name?: string | null };
export type SessionInfo = {
  mode: "local" | "remote";
  user_id: string;
  email?: string | null;
  display_name?: string | null;
  cloud_account?: CloudProfile | null;
};

export type SessionAccount = { initial: string; name: string; detail: string | null };

export function sessionAccount(session: SessionInfo | null): SessionAccount | null {
  if (!session) return null;
  const identity: CloudProfile | null | undefined = session.mode === "local" ? session.cloud_account : session;
  if (!identity) return null;
  const email = identity.email?.trim() || null;
  const name = identity.display_name?.trim() || email;
  if (!name) return null;
  return {
    initial: Array.from(name)[0].toUpperCase(),
    name,
    detail: email && email !== name ? email : null,
  };
}
