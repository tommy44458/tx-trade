// Every local API call goes through apiFetch. The desktop app uses its own local
// server; the remote page swaps in a transport that reaches the same routes on the
// user's computer through the txinTrade cloud relay.

export type ApiTransport = (path: string, init?: RequestInit) => Promise<Response>;

/** A server-sent event stream: `state` events, and an error or end that ends it. */
export type EventStreamHandlers = { onState: (data: string) => void; onError: () => void };
export type EventStreamOpener = (path: string, handlers: EventStreamHandlers) => () => void;

let transport: ApiTransport = (path, init) => fetch(path, init);
let streams: EventStreamOpener = (path, handlers) => {
  const source = new EventSource(path);
  source.addEventListener("state", (event) => handlers.onState((event as MessageEvent<string>).data));
  source.onerror = () => handlers.onError();
  return () => source.close();
};
let remote = false;

export function setRemoteTransport(next: ApiTransport, opener: EventStreamOpener): void {
  transport = next;
  streams = opener;
  remote = true;
}

/** Remote screens hide what only makes sense at the computer (keys, AI sign-in, updates). */
export const isRemoteMode = (): boolean => remote;

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return transport(path, init);
}

/** Open a local event stream; returns a function that closes it. */
export function openEventStream(path: string, handlers: EventStreamHandlers): () => void {
  return streams(path, handlers);
}
