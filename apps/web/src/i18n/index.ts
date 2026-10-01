import i18next from "i18next";
import { useEffect } from "react";
import { initReactI18next, useTranslation } from "react-i18next";
import { enUS, textNamespace, zhTW, type UiTextKey } from "./resources.ts";

export const UI_LOCALES = ["zh-TW", "en-US"] as const;
export type UiLocale = (typeof UI_LOCALES)[number];
export const isUiLocale = (value: unknown): value is UiLocale =>
  value === "zh-TW" || value === "en-US";

export const i18n = i18next.createInstance();
void i18n.use(initReactI18next).init({
  resources: { "zh-TW": zhTW, "en-US": enUS },
  lng: "zh-TW", fallbackLng: "zh-TW", supportedLngs: [...UI_LOCALES],
  defaultNS: "common", ns: Object.keys(zhTW),
  keySeparator: false, nsSeparator: false, initAsync: false,
  interpolation: { escapeValue: false }, react: { useSuspense: false },
});

export function uiLocale(): UiLocale {
  return isUiLocale(i18n.resolvedLanguage) ? i18n.resolvedLanguage : "zh-TW";
}

/** Only application-owned copy is translated. Never pass model/source text here. */
export function uiText(key: UiTextKey, values: Record<string, unknown> = {}): string {
  return i18n.t(key, { ns: textNamespace[key], ...values });
}

/** React subscribes at the app boundary; child views render with the new locale. */
export function useUiLocale(): UiLocale {
  useTranslation("common", { i18n });
  const locale = uiLocale();
  useEffect(() => {
    if (typeof document !== "undefined") document.documentElement.lang = locale;
  }, [locale]);
  return locale;
}

export async function setUiLocale(locale: UiLocale) {
  await i18n.changeLanguage(locale);
  if (typeof document !== "undefined") document.documentElement.lang = locale;
  if (typeof window !== "undefined" && window.tradeHelper?.updateLocale) {
    await window.tradeHelper.updateLocale(locale);
  }
}

export function languageName(locale: unknown): string {
  return locale === "en-US" ? "English" : "繁體中文";
}
