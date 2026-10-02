import { uiText } from "./i18n/index.ts";
import type { FormEvent } from "react";
import SelectControl from "./SelectControl";
import LeverageControl from "./LeverageControl";
import PositionOptionalFields, {
  type OptionalPositionFields,
} from "./PositionOptionalFields";

export type EditFields = OptionalPositionFields & {
  side: "long" | "short";
  leverage: number;
  margin_mode: "isolated" | "cross";
  entry_price: string;
  quantity: string;
  stop_loss: string;
  take_profit: string;
};

export default function EditPositionForm({
  value,
  onChange,
  onSubmit,
  onCancel,
  exchangeSynced = false,
}: {
  value: EditFields;
  onChange: (next: EditFields) => void;
  onSubmit: (event: FormEvent) => void;
  onCancel: () => void;
  exchangeSynced?: boolean;
}) {
  return (
    <form className="position-form position-editor" onSubmit={onSubmit}>
      <h3>{uiText("修改持倉")}</h3>
      {exchangeSynced && (
        <p className="note">{uiText("匯入持倉的方向、槓桿、數量及進場價由交易所同步；這裡只補充止損、止盈與備註。")}</p>
      )}{" "}
      {!exchangeSynced && (
        <div className="position-fields">
          <label>{uiText("方向")}<SelectControl
              value={value.side}
              onChange={(event) =>
                onChange({
                  ...value,
                  side: event.target.value as EditFields["side"],
                })
              }
            >
              <option value="long">{uiText("做多")}</option>
              <option value="short">{uiText("做空")}</option>
            </SelectControl>
          </label>
          <LeverageControl
            value={value.leverage}
            onChange={(leverage) => onChange({ ...value, leverage })}
          />
          <label>{uiText("保證金模式")}<SelectControl
              value={value.margin_mode}
              onChange={(event) =>
                onChange({
                  ...value,
                  margin_mode: event.target.value as EditFields["margin_mode"],
                })
              }
            >
              <option value="isolated">{uiText("逐倉")}</option>
              <option value="cross">{uiText("全倉")}</option>
            </SelectControl>
          </label>
        </div>
      )}{" "}
      {(!exchangeSynced
        ? (["entry_price", "quantity", "stop_loss", "take_profit"] as const)
        : (["stop_loss", "take_profit"] as const)
      ).map((key, index) => (
        <label key={key}>
          {key === "stop_loss"
            ? uiText("止損價（選填）")
            : key === "take_profit"
              ? uiText("止盈價（選填）")
              : key === "entry_price"
                ? uiText("平均進場價（USDT）")
                : uiText("持有數量（幣）")}
          <input
            name={key}
            required={!exchangeSynced && index < 2}
            inputMode="decimal"
            value={value[key]}
            onChange={(event) =>
              onChange({ ...value, [key]: event.target.value })
            }
          />
        </label>
      ))}{" "}
      {!exchangeSynced && (
        <PositionOptionalFields
          value={value}
          onChange={(patch) => onChange({ ...value, ...patch })}
        />
      )}{" "}
      {exchangeSynced && (
        <label>{uiText("備註（選填）")}<textarea
            maxLength={500}
            rows={3}
            value={value.notes}
            onChange={(event) =>
              onChange({ ...value, notes: event.target.value })
            }
          />
        </label>
      )}
      <div className="editor-actions">
        <button className="action" type="submit">{uiText("儲存修改")}</button>
        <button type="button" onClick={onCancel}>{uiText("取消")}</button>
      </div>
      <p className="note">{uiText("修改僅更新本地紀錄，不會更改交易所委託或自動執行分析。既有報告會標示過期。")}</p>
    </form>
  );
}
