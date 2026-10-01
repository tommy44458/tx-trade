import { uiLocale, type UiLocale } from "./i18n/index.ts";

export type PythonReferenceContext = {
  locale?: UiLocale;
  type?: string;
  kind?: string;
  tool?: string;
  origin?: "python" | "agent" | "source" | "user";
};

// Only reviewed application-owned calculation templates are projected for display.
// Legacy implementation terms become ordinary product copy in both UI languages.
// AI explanations, articles, user notes, raw records, and unknown sentences stay
// unchanged. Exact matching keeps this presentation change from rewriting advice.
const LEGACY_DISPLAY_COPY: Readonly<Record<string, { zhTW: string; enUS: string }>> = Object.freeze({
  "Python 提供": {
    "zhTW": "已計算",
    "enUS": "Calculated"
  },
  "AI 未完成，本次為 Python 備援結果：": {
    "zhTW": "AI 未完成，本次為計算備援結果：",
    "enUS": "AI did not complete. This result uses a calculation fallback:"
  },
  "指標與 Python 工具": {
    "zhTW": "指標與計算依據",
    "enUS": "Indicators and calculation evidence"
  },
  "Python 工具紀錄": {
    "zhTW": "計算紀錄",
    "enUS": "Calculation log"
  },
  "官方 FOMC 聲明發布後 15 分鐘內，Python 風控暫停新進場候選；未依聲明標題判定政策方向。": {
    "zhTW": "官方 FOMC 聲明發布後 15 分鐘內，風險規則暫停新進場候選；未依聲明標題判定政策方向。",
    "enUS": "Within 15 minutes of an official FOMC statement, risk rules pause new-entry candidates. The statement title is not used to determine policy direction."
  },
  "Python 已先算好常用指標，一次提供給 Agent 判斷。下表使用已收盤 K 線，盤中現價另行分析。": {
    "zhTW": "常用指標已預先計算，一次提供給 AI 判斷。下表使用已收盤 K 線，盤中現價另行分析。",
    "enUS": "Common indicators are precomputed for the AI to assess together. The table uses closed candles; the current intrabar price is analyzed separately."
  },
  "趨勢標籤只供參考；相關指標不代表多份獨立證據。完整精度、參數與確認時間可在下方 Python 紀錄查核。": {
    "zhTW": "趨勢標籤只供參考；相關指標不代表多份獨立證據。完整精度、參數與確認時間可在下方計算紀錄查核。",
    "enUS": "Trend labels are references only. Correlated indicators do not count as independent evidence. Full precision, parameters, and confirmation times are in the calculation log below."
  },
  "查看 Python 策略候選與限制（僅供參考，Agent 判斷以上方分析為準）": {
    "zhTW": "查看策略候選與限制（僅供參考，AI 判斷以上方分析為準）",
    "enUS": "View strategy candidates and limits (reference only; the AI's decision is in the analysis above)"
  },
  "。Agent 依市場證據判斷是否續抱；Python 風險估算僅供參考。": {
    "zhTW": "。AI 依市場證據判斷是否續抱；風險估算僅供參考。",
    "enUS": ". The AI decides whether to hold using market evidence. Risk estimates are references only."
  },
  "查看 Python 風險規則參考": {
    "zhTW": "查看風險估算參考",
    "enUS": "View risk-estimate reference"
  },
  "Python 工具驗證": {
    "zhTW": "指標計算檢核",
    "enUS": "Indicator calculation checks"
  },
  "Python 候選已通過方向、區間邊界與成本後風報比檢核；目前只列條件式情境，仍須依所選進場風格核對觸發條件。": {
    "zhTW": "參考情境已通過方向、區間邊界與成本後風報比檢核；目前只列條件式情境，仍須依所選進場風格核對觸發條件。",
    "enUS": "Reference scenarios passed direction, zone-boundary, and cost-adjusted reward/risk checks. They remain conditional; check the trigger for the selected entry style."
  },
  "Python 候選未通過方向、區間或風險檢核，因此維持觀望；等跨週期與價格條件重新確認後再分析。": {
    "zhTW": "參考情境未通過方向、區間或風險檢核，因此維持觀望；等跨週期與價格條件重新確認後再分析。",
    "enUS": "Reference scenarios did not pass direction, zone, or risk checks, so this reference remains on standby. Reanalyze after timeframe and price conditions are confirmed again."
  }
});

const FALLBACK_STYLES: Readonly<Record<string, string>> = Object.freeze({
  "": "",
  "本次採左側提前區間測試，尚無收盤確認；": "This is an anticipatory zone test, without closing confirmation. ",
  "本次採右側確認方式，須等待所選週期收盤；": "This confirmation entry requires the selected timeframe's close. ",
  "未指定左／右側，沿用一般條件式情境；": "No entry style was selected; general conditional scenarios are used. ",
});
const FALLBACK_RELATIONS: Readonly<Record<string, string>> = Object.freeze({
  "": "",
  "兩週期方向一致。": " The two timeframe directions agree.",
  "兩週期方向相反，策略觀望。": " The two timeframe directions conflict, so this reference waits.",
  "兩週期方向皆混合，僅檢查區間候選。": " Both timeframe directions are mixed; only range scenarios are checked.",
  "跨週期方向未確認，策略觀望。": " Cross-timeframe direction is unconfirmed, so this reference waits.",
  "另一週期資料未提供。": " Data for the other timeframe was not supplied.",
});
const FALLBACK_STRATEGIES = [
  "Python 候選已通過方向、區間邊界與成本後風報比檢核；目前只列條件式情境，仍須依所選進場風格核對觸發條件。",
  "Python 候選未通過方向、區間或風險檢核，因此維持觀望；等跨週期與價格條件重新確認後再分析。",
] as const;

const LEGACY_ENGLISH_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  "Python provides": "Python 提供",
  "AI did not complete. This result uses Python fallback:": "AI 未完成，本次為 Python 備援結果：",
  "Indicators and Python tools": "指標與 Python 工具",
  "Python tool log": "Python 工具紀錄",
  "Within 15 minutes of an official FOMC statement, Python risk rules pause new-entry candidates. The statement title is not used to determine policy direction.": "官方 FOMC 聲明發布後 15 分鐘內，Python 風控暫停新進場候選；未依聲明標題判定政策方向。",
  "Python precomputes common indicators for the Agent in one batch. The table uses closed candles; the current intrabar price is analyzed separately.": "Python 已先算好常用指標，一次提供給 Agent 判斷。下表使用已收盤 K 線，盤中現價另行分析。",
  "Trend labels are references only. Correlated indicators do not count as independent evidence. Full precision, parameters, and confirmation times are in the Python log below.": "趨勢標籤只供參考；相關指標不代表多份獨立證據。完整精度、參數與確認時間可在下方 Python 紀錄查核。",
  "View Python strategy candidates and limits (reference only; the Agent's decision is in the analysis above)": "查看 Python 策略候選與限制（僅供參考，Agent 判斷以上方分析為準）",
  ". The Agent decides whether to hold using market evidence. Python risk estimates are references only.": "。Agent 依市場證據判斷是否續抱；Python 風險估算僅供參考。",
  "View Python risk-rule reference": "查看 Python 風險規則參考",
  "Python tool validation": "Python 工具驗證",
});

function legacyDisplayCopy(text: string, locale: UiLocale): string | undefined {
  const legacyKey = Object.hasOwn(LEGACY_ENGLISH_ALIASES, text) ? LEGACY_ENGLISH_ALIASES[text] : text;
  if (Object.hasOwn(LEGACY_DISPLAY_COPY, legacyKey)) {
    const copy = LEGACY_DISPLAY_COPY[legacyKey];
    return locale === "en-US" ? copy.enUS : copy.zhTW;
  }
  // Old saved rules-only strategy paragraphs concatenate these known templates.
  // Any extra prose or price makes the paragraph unknown and preserves it intact.
  for (const [style, englishStyle] of Object.entries(FALLBACK_STYLES)) {
    for (const strategy of FALLBACK_STRATEGIES) {
      for (const [relation, englishRelation] of Object.entries(FALLBACK_RELATIONS)) {
        if (text === style + strategy + relation) {
          const copy = LEGACY_DISPLAY_COPY[strategy];
          return locale === "en-US" ? englishStyle + copy.enUS + englishRelation
            : style + copy.zhTW + relation;
        }
      }
    }
  }
  return undefined;
}

const ENGLISH: Readonly<Record<string, string>> = Object.freeze({
  "等待 FOMC 聲明消化": "Wait for the FOMC statement to be absorbed",
  "官方 FOMC 聲明發布後 15 分鐘內暫不產生新進場候選；尚未判讀政策方向": "No new entry templates during the first 15 minutes after the official FOMC statement; policy direction has not been interpreted",
  "等待事件結果": "Wait for the event outcome",
  "重大事件結果未確認，暫不產生新進場候選": "The major event outcome is unconfirmed; no new entry templates are generated yet",
  "等待跨週期資料": "Wait for cross-timeframe data",
  "缺少已收盤的另一週期行情，無法核對背景方向": "The other timeframe's closed candles are missing, so its background direction cannot be checked",
  "等待週期方向一致": "Wait for timeframe directions to agree",
  "等待方向確認": "Wait for direction confirmation",
  "其中一個週期的均線與收盤價尚未形成一致趨勢": "Moving averages and the closed price on one timeframe do not yet show a consistent trend",
  "等待確認以符合低風險傾向": "Wait for confirmation to match the low-risk preference",
  "左側提前測試尚無收盤確認，與本次低風險傾向衝突；請等待右側確認或調整偏好": "An anticipatory zone test has no closing confirmation and conflicts with this low-risk preference; wait for confirmation or adjust the preference",
  "你的看法與目前趨勢不同": "Your view differs from the current trend",
  "低風險傾向先等待新的收盤確認": "The low-risk preference waits for new closing confirmation",
  "價位依據不足": "Insufficient price-level evidence",
  "缺少合格且已確認的 v3 支撐或壓力區": "A qualifying confirmed v3 support or resistance zone is missing",
  "等待回到區間內": "Wait for price to return inside the range",
  "現價已在兩側 v3 候選區之外，不能假設區間持續有效": "Current price is outside the two v3 candidate zones; continued range validity cannot be assumed",
  "等待更好的條件": "Wait for better conditions",
  "左側提前區間測試仍未通過 v3 區間、距離或示例成本後風報比檢核": "The anticipatory zone test has not met the v3 zone, distance, or example cost-adjusted reward/risk checks",
  "目前 v3 區間、距離與示例成本後風報比未同時達標": "The v3 zone, distance, and example cost-adjusted reward/risk criteria are not all met",
  "價格進入已確認 v3 支撐區、尚未觸及失效止損；屬提前測試，無收盤反轉確認": "Price enters a confirmed v3 support zone without reaching the invalidation stop; this is an early test, without a confirmed reversal at the close",
  "價格進入已確認 v3 壓力區、尚未觸及失效止損；屬提前測試，無收盤反轉確認": "Price enters a confirmed v3 resistance zone without reaching the invalidation stop; this is an early test, without a confirmed reversal at the close",
  "價格跌破支撐下緣或觸及失效止損": "Price falls below the support zone's lower boundary or reaches the invalidation stop",
  "價格突破壓力上緣或觸及失效止損": "Price rises above the resistance zone's upper boundary or reaches the invalidation stop",
  "下一根已收盤 K 線收於 v3 壓力上緣之上，盤中穿越不算確認": "The next closed candle finishes above the v3 resistance zone's upper boundary; an intrabar crossing is not confirmation",
  "下一根已收盤 K 線收於 v3 支撐下緣之下，盤中穿越不算確認": "The next closed candle finishes below the v3 support zone's lower boundary; an intrabar crossing is not confirmation",
  "收盤回到突破區內，或價格觸及失效止損": "The close returns inside the breakout zone, or price reaches the invalidation stop",
  "下一根已收盤 K 線測試支撐並收回區間上緣": "The next closed candle tests support and closes back at the zone's upper boundary",
  "下一根已收盤 K 線測試壓力並收回區間下緣": "The next closed candle tests resistance and closes back at the zone's lower boundary",
  "收盤跌破支撐下緣，或價格觸及失效止損": "A close falls below the support zone's lower boundary, or price reaches the invalidation stop",
  "收盤突破壓力上緣，或價格觸及失效止損": "A close rises above the resistance zone's upper boundary, or price reaches the invalidation stop",
  "官方經濟日程來源離線或不完整，不能聲稱已排除事件風險": "The official economic-calendar source is offline or incomplete; event risk cannot be considered ruled out",
  "已核對官方日程；Fed 標題消息另有來源狀態，指標實際值尚未接入": "The official schedule was checked; Fed headlines have a separate source status, and indicator actuals were not integrated into this reference",
  "新聞與經濟事件尚未接入，不能聲稱已排除事件風險": "News and economic events were not integrated into this reference; event risk cannot be considered ruled out",
  "情境方向與你的判斷相反": "The scenario direction conflicts with your view",
  "左側區間測試尚無收盤確認，價格可能直接穿透支撐或壓力": "This anticipatory zone test has no closing confirmation; price may pass directly through support or resistance",
  "風報比採明示示例手續費與不利滑價，非實際帳戶費率；未計資金費或強平": "Reward/risk uses disclosed example fees and adverse slippage, not actual account fees; funding and liquidation are excluded",
  "合格 v3 區間提供較靠近現價的保護參考；調整前須核對觸發價格與可能提前出場。": "A qualifying v3 zone offers a protection reference closer to the current price; verify the trigger price and the possibility of an earlier exit before adjusting.",
  "參考標記價已達手動登記的交易所強平價；先向交易所核對部位狀態，不能假定已強平。": "The reference mark price has reached the manually recorded exchange liquidation price; verify the position with the exchange rather than assuming liquidation occurred.",
  "目前參考價格已達登記止損；先向交易所核對委託與實際成交，不能假定已平倉。": "The reference price has reached the recorded stop; verify the order and actual fill with the exchange rather than assuming the position closed.",
  "目前參考價格已達登記止盈；先向交易所核對委託與實際成交，不能假定已平倉。": "The reference price has reached the recorded target; verify the order and actual fill with the exchange rather than assuming the position closed.",
  "登記的止損與止盈尚未觸及；維持原條件並等待下一次已收盤資料確認。": "The recorded stop and target have not been reached; retain the existing conditions and wait for the next closed-candle data to confirm.",
  "跨週期市場方向與部位相反；可評估降低曝險或退出，但沒有帳戶權益時不指定減倉比例。": "The cross-timeframe market direction opposes the position; consider reducing exposure or exiting, but no reduction percentage is specified without account equity.",
  "跨週期市場方向與部位相反；可評估降低曝險或退出；仍缺少明確風險預算與成交條件，因此不指定減倉比例。": "The cross-timeframe market direction opposes the position; consider reducing exposure or exiting. A clear risk budget and execution conditions are still missing, so no reduction percentage is specified.",
  "目前處於重大事件風險窗口；可評估降低曝險，仍須自行核對交易所部位與成交。": "This is a major-event risk window; consider reducing exposure, but the exchange position and actual fills still need to be checked.",
  "當次個人方向判斷與持倉相反；可評估降低曝險，但個人看法不改變客觀市場數值。": "The submitted personal directional view opposes the position; consider reducing exposure, but that view does not change objective market values.",
  "低風險偏好遇到跨週期不確定性；可評估降低曝險，不指定減倉比例。": "The low-risk preference faces cross-timeframe uncertainty; consider reducing exposure without specifying a reduction percentage.",
  "參考標記價已達紀錄中的交易所強平價；請立即向交易所核對實際部位狀態": "The reference mark price has reached the recorded exchange liquidation price; verify the actual position status with the exchange immediately",
  "尚未設定止損": "No stop is recorded",
  "目前價格已達登記止損，請確認實際成交": "Price has reached the recorded stop; check the actual fill",
  "登記止損位於進場價的獲利側；僅為價格條件，不保證成交或鎖定獲利": "The recorded stop is on the profitable side of entry; this is a price condition and does not guarantee a fill or locked-in profit",
  "尚未設定止盈": "No target is recorded",
  "目前價格已達登記止盈，請確認實際成交": "Price has reached the recorded target; check the actual fill",
  "手動登記部位；保證金與報酬率僅為理論估算，不含費用、資金費或維持保證金；強平價只顯示用戶手動登記的交易所數值，不代表即時帳戶狀態": "Manually recorded position. Margin and return are theoretical estimates excluding fees, funding, and maintenance margin. The liquidation price is the user-recorded exchange value, not live account status",
  "確認目前方向與均線排列": "Check the current direction and moving-average ordering",
  "衡量近期波動，避免把單一價格當成支撐壓力": "Measure recent volatility rather than treating a single price as support or resistance",
  "取得已確認的支撐與壓力區間": "Obtain confirmed support and resistance zones",
  "比較最新成交量與近期基準": "Compare the latest volume with the recent baseline",
  "檢查合約標記價格與資金費率背景": "Check the contract mark price and funding-rate context",
  "核對主週期與上層已收盤趨勢是否一致": "Check whether closed-candle trends on the primary and higher timeframe agree",
  "核對多空方向、進場、止損與目標是否形成有效情境": "Check whether direction, entry, stop, and target form a valid reference scenario",
  "核對所選持倉的保護、結構與可選動作": "Review protection, structure, and reference actions for the selected positions",
  "預先計算主週期與向上三個週期的常用指標，供 Agent 一次綜合判斷": "Precompute common indicators on the primary and three higher timeframes for the Agent to assess together",
  "取得資金費與標記價背景": "Obtain funding and mark-price context",
  "取得主週期已確認 v3 支撐壓力": "Obtain confirmed v3 support and resistance on the primary timeframe",
  "以相同快照比較主週期與向上三個週期已收盤方向": "Compare closed-candle directions on the primary and three higher timeframes using the same snapshot",
  "預先提供數值檢查情境，供 Agent 自主取捨": "Precompute numerically checked reference scenarios for the Agent's independent decision",
  "預先計算所選持倉參考資料": "Precompute reference data for the selected positions",
  "僅已核對的官方宏觀公布值和 FOMC 決策可作宏觀背景；SEC 摘要與 Jev 分類尚未成為策略方向證據，市場共識預期值未接入": "Only checked official macro actuals and FOMC decisions can supply macro context. SEC summaries and Jev classifications are not directional strategy evidence; consensus forecasts were not integrated into this reference",
  "官方宏觀實際值依來源可用性納入；市場共識預期值及廣泛新聞尚未接入": "Official macro actuals are included when sources are available; consensus forecasts and broad news were not integrated into this reference",
  "新聞與美國經濟事件尚未接入": "News and US economic events were not integrated into this reference",
  "委託簿為單次近價快照，掛單可撤銷，不能推論多空持倉或長期支撐": "The order book is a single snapshot near the quote. Orders can be canceled and do not establish long/short positions or long-term support",
  "v3 阻滯回測不等於策略績效或反轉機率": "The v3 friction backtest is not a measure of strategy performance or reversal probability",
  "策略風報比含示例費用與滑價；實際費率、成交與資金費可能不同": "Strategy reward/risk includes example fees and slippage; actual rates, fills, and funding may differ",
  "持倉損益未扣費用或資金費": "Position profit/loss excludes fees and funding",
  "未計算強平價": "Liquidation price was not calculated",
  "趨勢與 v3 支撐壓力只使用已收盤 K 線；現價及未收盤 K 線僅作盤中觀察": "Trends and v3 support/resistance use only closed candles; current price and unclosed candles are intrabar observations",
  "支撐區再次評估": "Reassess the support zone",
  "壓力區再次評估": "Reassess the resistance zone",
  "分析時價格已在此區間；可核對最新報價後再次分析": "Price was inside this zone at analysis time; check a new quote before analyzing again",
  "價格進入此區間時，再按下分析": "Run the analysis again when price enters this zone",
  "檢查是否收回區間、量能是否改變，並重新核對另一週期方向、有效止損距離與成本後風報比。到價只代表重新評估，不代表進場。": "Check whether price reclaims the zone and volume changes, then reassess the other timeframe, effective stop distance, and cost-adjusted reward/risk. Reaching the zone means reassess, not enter.",
  "若價格直接穿透區間，重新分析區間是否失效；不要沿用本次的支撐壓力判斷。": "If price passes directly through the zone, reassess whether it is invalidated; do not carry forward this support/resistance judgment.",
  "下一根收盤後更新": "Update after the next candle closes",
  "所選週期下一根 K 線收盤後，再按下分析": "Run the analysis again after the next candle on the selected timeframe closes",
  "用新收盤資料核對趨勢、區間生命週期與候選觸發條件；若資料不足，繼續等待。": "Use new closed-candle data to check the trend, zone lifecycle, and candidate triggers; wait if the data is insufficient.",
  "報告到期或重大事件、部位保護條件改變時，原建議也需要重新評估。": "Reassess the original advice when the report expires, a major event occurs, or position-protection conditions change.",
});

const SCENARIOS: Readonly<Record<string, string>> = Object.freeze({
  "趨勢回調": "Trend pullback",
  "突破確認": "Breakout confirmation",
  "區間測試": "Range test",
});

function templateTranslation(text: string): string | undefined {
  const title = /^(趨勢回調|突破確認|區間測試)(做多|做空)情境$/.exec(text);
  if (title) return `${SCENARIOS[title[1]]} ${title[2] === "做多" ? "long" : "short"} scenario`;
  const early = /^(趨勢回調|突破確認|區間測試)為左側提前區間測試；v3 區間可能產生阻力，也可能被直接穿透，尚無收盤確認。$/.exec(text);
  if (early) return `${SCENARIOS[early[1]]} is an anticipatory zone test. A v3 zone may create friction or be passed through directly; there is no closing confirmation yet.`;
  const confirmation = /^(趨勢回調|突破確認|區間測試)僅是有條件的價格阻力情境；v3 區間不保證反轉或突破，需等待所選週期收盤確認。$/.exec(text);
  if (confirmation) return `${SCENARIOS[confirmation[1]]} is only a conditional price-friction scenario. A v3 zone guarantees neither reversal nor breakout; confirmation at the selected timeframe's close is still required.`;
  const disagreement = /^(1H|4H|12H|1D) 與 (4H|12H|1D|3D) 的已收盤趨勢相反，先不推薦進場$/.exec(text);
  if (disagreement) return `The closed-candle trends on ${disagreement[1]} and ${disagreement[2]} oppose each other; this reference does not recommend an entry yet`;
  const protection = /^手動部位尚缺(止損與止盈|止損|止盈)；請先檢查保護條件，系統不自動補造價位。$/.exec(text);
  if (protection) {
    const missing = { "止損與止盈": "a stop and target", "止損": "a stop", "止盈": "a target" }[protection[1]];
    return `The manual position is missing ${missing}; review its protection conditions. The system does not invent prices.`;
  }
  return undefined;
}

export function pythonReferenceText(
  text: string | null | undefined,
  context: PythonReferenceContext = {},
): string {
  const original = text ?? "";
  if (context.origin && context.origin !== "python") return original;
  const locale = context.locale ?? uiLocale();
  const displayCopy = legacyDisplayCopy(original, locale);
  if (displayCopy !== undefined) return displayCopy;
  if (locale !== "en-US") return original;
  return Object.hasOwn(ENGLISH, original) ? ENGLISH[original] : templateTranslation(original) ?? original;
}

export function pythonReferenceLabels(
  texts: readonly string[],
  context: PythonReferenceContext = {},
): string[] {
  return texts.map((text) => pythonReferenceText(text, context));
}
