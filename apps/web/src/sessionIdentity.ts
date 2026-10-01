// The local app has one internal workspace owner (for example "local-demo")
// that is not a person and is never shown. A remote session signed in with an
// account (such as Google) reports who is connected, and the sidebar shows it.
export type SessionInfo = {
  mode: "local" | "remote";
  user_id: string;
  email?: string | null;
  display_name?: string | null;
};

export type SessionAccount = { initial: string; name: string; detail: string | null };

export function sessionAccount(session: SessionInfo | null): SessionAccount | null {
  if (!session || session.mode === "local") return null;
  const email = session.email?.trim() || null;
  const name = session.display_name?.trim() || email;
  if (!name) return null;
  return {
    initial: Array.from(name)[0].toUpperCase(),
    name,
    detail: email && email !== name ? email : null,
  };
}
