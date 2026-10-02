/** Sent when the cloud account changes outside the Settings section, e.g. sign-out from the account menu. */
export const CLOUD_ACCOUNT_CHANGED = "txintrade:cloud-account-changed";

export function announceCloudAccountChange(): void {
  window.dispatchEvent(new Event(CLOUD_ACCOUNT_CHANGED));
}
