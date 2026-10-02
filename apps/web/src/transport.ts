// Every local API call goes through apiFetch. The desktop app uses its own local
// server; the remote page swaps in a transport that reaches the same routes on the
// user's computer through the txinTrade cloud relay.

export type ApiTransport = (path: string, init?: RequestInit) => Promise<Response>;

let transport: ApiTransport = (path, init) => fetch(path, init);
let remote = false;

export function setRemoteTransport(next: ApiTransport): void {
  transport = next;
  remote = true;
}

/** Remote screens hide what only makes sense at the computer (keys, AI sign-in, updates). */
export const isRemoteMode = (): boolean => remote;

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return transport(path, init);
}
