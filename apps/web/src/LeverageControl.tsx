import { uiText } from "./i18n/index.ts";
import { useEffect, useId, useRef, useState } from "react";
import SelectControl from "./SelectControl";
import {
  LEVERAGE_PRESETS,
  MAX_LEVERAGE,
  MIN_LEVERAGE,
  validateLeverageInput,
} from "./leverage";
import "./LeverageControl.css";

export type LeverageControlProps = {
  value: number;
  onChange: (value: number) => void;
  label?: string;
  id?: string;
  min?: number;
  max?: number;
  disabled?: boolean;
  className?: string;
  onValidityChange?: (valid: boolean) => void;
};

export default function LeverageControl({
  value,
  onChange,
  label = uiText("槓桿"),
  id,
  min = MIN_LEVERAGE,
  max = MAX_LEVERAGE,
  disabled = false,
  className = "",
  onValidityChange,
}: LeverageControlProps) {
  const generatedId = useId();
  const inputId = id ?? `${generatedId}-leverage`;
  const hintId = `${inputId}-hint`;
  const [draft, setDraft] = useState(String(value));
  const input = useRef<HTMLInputElement>(null);
  const externalValue = useRef(value);
  const localCommit = useRef<number | null>(null);
  const validation = validateLeverageInput(draft, min, max);
  const error = disabled ? null : validation.error;
  const presets = LEVERAGE_PRESETS.filter((preset) => preset >= min && preset <= max);
  const selected = validation.value !== null && presets.some((preset) => preset === validation.value)
    ? String(validation.value)
    : "custom";

  useEffect(() => {
    if (externalValue.current === value) return;
    externalValue.current = value;
    // A parent update caused by this keystroke keeps the editable text intact.
    // Loading another position or saved preference replaces it with that value.
    if (localCommit.current !== value) setDraft(String(value));
    localCommit.current = null;
  }, [value]);

  useEffect(() => {
    input.current?.setCustomValidity(error ?? "");
    onValidityChange?.(!error);
  }, [error, onValidityChange]);

  function update(raw: string) {
    const next = validateLeverageInput(raw, min, max);
    input.current?.setCustomValidity(disabled ? "" : next.error ?? "");
    setDraft(raw);
    if (next.value !== null) {
      localCommit.current = next.value;
      onChange(next.value);
    }
  }

  return (
    <div className={`leverage-control ${error ? "leverage-control-invalid" : ""} ${className}`.trim()}>
      <label className="leverage-control-label" htmlFor={inputId}>{label}</label>
      <div className="leverage-control-fields">
        <SelectControl
          aria-label={uiText("{{p0}}常用倍數", { p0: label })}
          value={selected}
          disabled={disabled}
          onChange={(event) => {
            if (event.target.value === "custom") {
              input.current?.focus();
              input.current?.select();
            } else {
              update(event.target.value);
            }
          }}
        >
          {presets.map((preset) => <option key={preset} value={preset}>{preset}×</option>)}
          <option value="custom">{uiText("自訂")}</option>
        </SelectControl>
        <span className="leverage-control-number">
          <input
            ref={input}
            id={inputId}
            className="leverage-control-input"
            type="text"
            inputMode="numeric"
            required
            disabled={disabled}
            autoComplete="off"
            aria-invalid={Boolean(error)}
            aria-describedby={hintId}
            value={draft}
            onChange={(event) => update(event.target.value)}
            onBlur={() => {
              if (validation.value !== null) setDraft(String(validation.value));
            }}
          />
          <span className="leverage-control-unit" aria-hidden="true">×</span>
        </span>
      </div>
      <p id={hintId} className="leverage-control-hint" role={error ? "alert" : undefined}>
        {error ?? uiText("{{p0}}–{{p1}} 倍，可手動輸入整數", { p0: min, p1: max })}
      </p>
    </div>
  );
}
