import { uiText, uiLocale, useUiLocale } from "./i18n/index.ts";
import { UI_THEME_CHANGE_EVENT } from "./uiTheme";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  createChart,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type UTCTimestamp,
} from "lightweight-charts";
import {
  chartPrice,
  chartRefreshOffset,
  chartZones,
  currentChartPrice,
  isInvalidatedZone,
  nearestChartZones,
  normalizeCandles,
  zoneTitle,
  type ChartCandle,
  type ChartLevel,
  type ChartZone,
  type NormalizedCandle,
} from "./candlestickData";
import "./CandlestickChart.css";

export type { ChartCandle, ChartLevel } from "./candlestickData";

export type CandlestickChartProps = {
  candles: ChartCandle[];
  levels: ChartLevel[];
  quotePrice?: string | number;
  loading?: boolean;
  levelsLoading?: boolean;
  marketId: string;
  timeframe: string;
};

type ChartTheme = {
  background: string;
  text: string;
  grid: string;
  positive: string;
  negative: string;
  accent: string;
};

function readTheme(element: HTMLElement): ChartTheme {
  const style = getComputedStyle(element);
  const read = (key: string, fallback: string) =>
    style.getPropertyValue(key).trim() || fallback;
  return {
    background: read("--surface", "#fff"),
    text: read("--text-secondary", "#58585f"),
    grid: read("--border", "#e5e5ea"),
    positive: read("--positive", "#14774a"),
    negative: read("--negative", "#c13543"),
    accent: read("--accent", "#0066cc"),
  };
}

function volumeData(candle: NormalizedCandle, theme: ChartTheme) {
  const color = candle.close >= candle.open ? theme.positive : theme.negative;
  const hex = /^#([0-9a-f]{6})$/i.exec(color)?.[1];
  return {
    time: candle.time as UTCTimestamp,
    value: candle.volume ?? 0,
    color: hex ? `rgba(${parseInt(hex.slice(0, 2), 16)},${parseInt(hex.slice(2, 4), 16)},${parseInt(hex.slice(4, 6), 16)},0.35)` : color,
  };
}

// Series primitives keep the zone coordinates aligned during panning, price
// scaling, resize, and device-pixel-ratio changes without a DOM overlay.
class ZoneBands implements ISeriesPrimitive {
  private series?: SeriesAttachedParameter["series"];
  private requestUpdate?: () => void;
  private zones: ChartZone[] = [];
  private theme: ChartTheme;
  private readonly view: IPrimitivePaneView;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(theme: ChartTheme) {
    this.theme = theme;
    const renderer: IPrimitivePaneRenderer = {
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          if (!this.series) return;
          const labelRows: number[] = [];
          for (const zone of this.zones) {
            const highY = this.series.priceToCoordinate(zone.high);
            const lowY = this.series.priceToCoordinate(zone.low);
            if (highY === null || lowY === null) continue;
            const top = Math.min(highY, lowY);
            const bottom = Math.max(highY, lowY);
            if (bottom < 0 || top > mediaSize.height) continue;
            const invalidated = isInvalidatedZone(zone);
            const color = invalidated ? this.theme.text :
              zone.kind === "support" ? this.theme.positive : this.theme.negative;
            context.save();
            context.fillStyle = color;
            context.globalAlpha = invalidated ? 0.035 : zone.inside ? 0.18 : 0.07;
            context.fillRect(0, top, mediaSize.width, Math.max(2, bottom - top));
            context.globalAlpha = invalidated ? 0.3 : zone.inside ? 0.85 : 0.42;
            context.strokeStyle = color;
            context.lineWidth = zone.inside && !invalidated ? 1.5 : 1;
            context.setLineDash(zone.inside && !invalidated ? [] : [4, 4]);
            for (const y of [top, bottom]) {
              context.beginPath();
              context.moveTo(0, y);
              context.lineTo(mediaSize.width, y);
              context.stroke();
            }
            const labelY = Math.min(mediaSize.height - 8, Math.max(15, top + 15));
            if (!labelRows.some((used) => Math.abs(used - labelY) < 17)) {
              labelRows.push(labelY);
              context.setLineDash([]);
              context.globalAlpha = 1;
              context.font = '11px -apple-system, BlinkMacSystemFont, sans-serif';
              const label = `${invalidated ? uiText("原") : ""}${zoneTitle(zone)} ${chartPrice(zone.low)}–${chartPrice(zone.high)}${invalidated ? uiText("（已失效）") : zone.inside ? " " + uiText("· 現價在區間內") : ""}`;
              const width = context.measureText(label).width;
              context.fillStyle = this.theme.background;
              context.globalAlpha = 0.92;
              context.fillRect(5, labelY - 12, width + 12, 17);
              context.globalAlpha = 1;
              context.fillStyle = color;
              context.fillText(label, 11, labelY);
            }
            context.restore();
          }
        });
      },
    };
    this.view = { zOrder: () => "bottom", renderer: () => renderer };
    this.views = [this.view];
  }

  attached({ series, requestUpdate }: SeriesAttachedParameter) {
    this.series = series;
    this.requestUpdate = requestUpdate;
  }

  detached() {
    this.series = undefined;
    this.requestUpdate = undefined;
  }

  paneViews() {
    return this.views;
  }

  update(zones: ChartZone[], theme?: ChartTheme) {
    this.zones = zones;
    if (theme) this.theme = theme;
    this.requestUpdate?.();
  }
}

function candleDate(time: number) {
  return new Date(time * 1000).toLocaleString(uiLocale(), {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function dataEqual(a: NormalizedCandle, b: NormalizedCandle) {
  return a.time === b.time && a.open === b.open && a.high === b.high &&
    a.low === b.low && a.close === b.close && a.volume === b.volume;
}

export default function CandlestickChart({
  candles,
  levels,
  quotePrice,
  loading = false,
  levelsLoading = false,
  marketId,
  timeframe,
}: CandlestickChartProps) {
  const locale = useUiLocale();
  const canvasRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const bandsRef = useRef<ZoneBands | null>(null);
  const priceLineRef = useRef<IPriceLine | null>(null);
  const previousData = useRef<NormalizedCandle[]>([]);
  const data = useMemo(() => normalizeCandles(candles), [candles]);
  const price = currentChartPrice(quotePrice, data);
  const zones = useMemo(() => chartZones(levels, price), [levels, price]);
  const [showSupport, setShowSupport] = useState(true);
  const [showResistance, setShowResistance] = useState(true);
  const [showVolume, setShowVolume] = useState(true);
  const [hovered, setHovered] = useState<NormalizedCandle | null>(null);
  const [openedAt] = useState(() => Date.now() / 1000);
  const dataRef = useRef(data);
  const priceRef = useRef(price);
  const zonesRef = useRef<ChartZone[]>([]);
  const visibleZones = useMemo(() => nearestChartZones(zones, price).filter((zone) =>
    zone.kind === "support" ? showSupport : showResistance,
  ), [zones, price, showSupport, showResistance]);
  const selected = (hovered && data.find((candle) => candle.time === hovered.time)) || data.at(-1);
  const inside = zones.filter((zone) => zone.inside);
  const hasVolume = data.some((candle) => candle.volume !== undefined);

  useEffect(() => {
    dataRef.current = data;
    zonesRef.current = visibleZones;
    priceRef.current = price;
  }, [data, visibleZones, price]);

  function resetView() {
    const chart = chartRef.current;
    if (!chart || dataRef.current.length === 0) return;
    const count = dataRef.current.length;
    chart.priceScale("right").applyOptions({ autoScale: true });
    chart.timeScale().setVisibleLogicalRange({
      from: Math.max(-1, count - 72),
      to: count + 3,
    });
  }

  function zoom(factor: number) {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (!scale || !range) return;
    const middle = (range.from + range.to) / 2;
    const half = Math.max(4, Math.min(dataRef.current.length + 10,
      (range.to - range.from) * factor / 2));
    scale.setVisibleLogicalRange({ from: middle - half, to: middle + half });
  }

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    let theme = readTheme(element);
    const chart = createChart(element, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: theme.background },
        textColor: theme.text,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
      rightPriceScale: { borderColor: theme.grid, scaleMargins: { top: 0.08, bottom: 0.23 } },
      timeScale: {
        borderColor: theme.grid,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
        minBarSpacing: 2,
        shiftVisibleRangeOnNewBar: false,
        tickMarkFormatter: (time: unknown) => typeof time === "number" ? candleDate(time) : "",
      },
      localization: { locale: uiLocale(), timeFormatter: (time: unknown) => typeof time === "number" ? candleDate(time) : "" },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      kineticScroll: { mouse: false, touch: !matchMedia("(prefers-reduced-motion: reduce)").matches },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: theme.positive,
      downColor: theme.negative,
      wickUpColor: theme.positive,
      wickDownColor: theme.negative,
      borderVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
      autoscaleInfoProvider: (base: () => AutoscaleInfo | null) => {
        const info = base();
        if (!info?.priceRange) return info;
        const { minValue, maxValue } = info.priceRange;
        const padding = Math.max((maxValue - minValue) * 0.35, maxValue * 0.002);
        const nearby = zonesRef.current.filter((zone) => zone.high >= minValue - padding && zone.low <= maxValue + padding);
        const view = chart.timeScale().getVisibleLogicalRange();
        const latestInView = view && view.to >= dataRef.current.length - 1;
        const livePrice = latestInView ? priceRef.current : undefined;
        return {
          ...info,
          priceRange: {
            minValue: Math.min(minValue, livePrice ?? minValue, ...nearby.map((zone) => Math.max(minValue - padding, zone.low))),
            maxValue: Math.max(maxValue, livePrice ?? maxValue, ...nearby.map((zone) => Math.min(maxValue + padding, zone.high))),
          },
        };
      },
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ visible: false, scaleMargins: { top: 0.83, bottom: 0.02 } });
    const bands = new ZoneBands(theme);
    series.attachPrimitive(bands);
    chartRef.current = chart;
    candleRef.current = series;
    volumeRef.current = volumeSeries;
    bandsRef.current = bands;
    previousData.current = [];
    priceLineRef.current = null;
    setHovered(null);
    const onCrosshair: Parameters<IChartApi["subscribeCrosshairMove"]>[0] = (event) => {
      if (event.time === undefined || !event.point || event.point.x < 0 || event.point.y < 0) {
        setHovered(null);
        return;
      }
      const match = dataRef.current.find((item) => item.time === event.time);
      setHovered(match ?? null);
    };
    chart.subscribeCrosshairMove(onCrosshair);
    const onThemeChange = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      theme = readTheme(element);
      chart.applyOptions({
        layout: { background: { type: ColorType.Solid, color: theme.background }, textColor: theme.text },
        grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
        rightPriceScale: { borderColor: theme.grid },
        timeScale: { borderColor: theme.grid },
      });
      series.applyOptions({ upColor: theme.positive, downColor: theme.negative, wickUpColor: theme.positive, wickDownColor: theme.negative });
      volumeSeries.setData(dataRef.current.map(candle => volumeData(candle, theme)));
      priceLineRef.current?.applyOptions({ color: theme.accent });
      bands.update(zonesRef.current, theme);
      if (range) chart.timeScale().setVisibleLogicalRange(range);
    };
    window.addEventListener(UI_THEME_CHANGE_EVENT, onThemeChange);
    const stopWheel = (event: WheelEvent) => event.preventDefault();
    element.addEventListener("wheel", stopWheel, { passive: false });
    return () => {
      window.removeEventListener(UI_THEME_CHANGE_EVENT, onThemeChange);
      element.removeEventListener("wheel", stopWheel);
      chart.unsubscribeCrosshairMove(onCrosshair);
      series.detachPrimitive(bands);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      bandsRef.current = null;
      priceLineRef.current = null;
    };
  }, [marketId, timeframe]);

  useEffect(() => {
    const series = candleRef.current;
    const volumeSeries = volumeRef.current;
    const chart = chartRef.current;
    if (!series || !volumeSeries || !chart) return;
    const previous = previousData.current;
    const range = chart.timeScale().getVisibleLogicalRange();
    const firstData = previous.length === 0;
    const precision = price === undefined ? 2 : price >= 1000 ? 2 : price >= 1 ? 4 : price >= 0.01 ? 6 : 8;
    series.applyOptions({ priceFormat: { type: "price", precision, minMove: 10 ** -precision } });
    const candleData = (candle: NormalizedCandle) => ({ ...candle, time: candle.time as UTCTimestamp });
    const theme = readTheme(canvasRef.current!);
    const lastOnly = previous.length > 0 && data.length >= previous.length &&
      previous.slice(0, -1).every((candle, index) => data[index] && dataEqual(candle, data[index])) &&
      previous.at(-1)?.time === data[previous.length - 1]?.time;
    if (lastOnly) {
      for (const candle of data.slice(previous.length - 1)) {
        series.update(candleData(candle));
        volumeSeries.update(volumeData(candle, theme));
      }
    } else {
      series.setData(data.map(candleData));
      volumeSeries.setData(data.map(candle => volumeData(candle, theme)));
      if (!firstData && range) {
        const offset = chartRefreshOffset(previous, data);
        chart.timeScale().setVisibleLogicalRange({ from: range.from + offset, to: range.to + offset });
      }
    }
    previousData.current = data;
    if (firstData && data.length) resetView();
  }, [data, marketId, timeframe, price]);

  useEffect(() => {
    const series = candleRef.current;
    if (!series) return;
    if (price === undefined) {
      if (priceLineRef.current) series.removePriceLine(priceLineRef.current);
      priceLineRef.current = null;
      return;
    }
    if (priceLineRef.current) priceLineRef.current.applyOptions({ price, title: uiText("現價") });
    else priceLineRef.current = series.createPriceLine({
      price, color: readTheme(canvasRef.current!).accent, lineWidth: 1,
      lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: uiText("現價"),
    });
  }, [price, marketId, timeframe, locale]);

  useEffect(() => {
    bandsRef.current?.update(zonesRef.current);
    // Re-run only the scale calculation; the user's time viewport is retained.
    candleRef.current?.applyOptions({});
  }, [zones, showSupport, showResistance, marketId, timeframe, locale]);

  useEffect(() => {
    chartRef.current?.applyOptions({ localization: { locale } });
  }, [locale]);

  useEffect(() => {
    volumeRef.current?.applyOptions({ visible: showVolume && hasVolume });
    chartRef.current?.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: showVolume && hasVolume ? 0.23 : 0.08 } });
  }, [showVolume, hasVolume, marketId, timeframe]);

  const keyboard = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const scale = chartRef.current?.timeScale();
    const range = scale?.getVisibleLogicalRange();
    if (event.key === "+" || event.key === "=") zoom(0.8);
    else if (event.key === "-") zoom(1.25);
    else if (event.key === "Home" || event.key === "0") resetView();
    else if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && scale && range) {
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      const offset = (range.to - range.from) * 0.15 * direction;
      scale.setVisibleLogicalRange({ from: range.from + offset, to: range.to + offset });
    } else return;
    event.preventDefault();
  };

  return (
    <div className="candlestick-chart" data-market={marketId} data-timeframe={timeframe}>
      <div className="candlestick-toolbar">
        <div className="candlestick-toggles" aria-label={uiText("圖表顯示")}>
          <button type="button" className="candlestick-support" aria-pressed={showSupport} onClick={() => setShowSupport(!showSupport)}>{uiText("支撐")}</button>
          <button type="button" className="candlestick-resistance" aria-pressed={showResistance} onClick={() => setShowResistance(!showResistance)}>{uiText("壓力")}</button>
          <button type="button" aria-pressed={showVolume} disabled={!hasVolume} onClick={() => setShowVolume(!showVolume)}>{uiText("量能")}</button>
        </div>
        <div className="candlestick-view-controls">
          <button type="button" aria-label={uiText("縮小 K 線圖")} disabled={!data.length} onClick={() => zoom(1.25)}>−</button>
          <button type="button" aria-label={uiText("放大 K 線圖")} disabled={!data.length} onClick={() => zoom(0.8)}>＋</button>
          <button type="button" disabled={!data.length} onClick={resetView}>{uiText("重設視圖")}</button>
        </div>
      </div>
      <div className="candlestick-readout" aria-label={uiText("K 線數值")}>
        <span className="candlestick-time">{selected ? candleDate(selected.time) : "—"} <small>{selected ? (selected.closed ?? selected.closeTime <= openedAt) ? uiText("已收盤") : uiText("未收盤") : uiText("K 線時間")}</small></span>
        {[ [uiText("開"), selected?.open], [locale === "en-US" ? "H" : uiText("高"), selected?.high], [locale === "en-US" ? "L" : uiText("低"), selected?.low], [uiText("收"), selected?.close] ].map(([label, value]) => (
          <span key={label}><small>{label}</small><strong>{typeof value === "number" ? chartPrice(value) : "—"}</strong></span>
        ))}
        <span><small>{uiText("量")}</small><strong>{selected?.volume !== undefined ? chartPrice(selected.volume) : "—"}</strong></span>
      </div>
      <div
        className="candlestick-canvas-shell"
        tabIndex={0}
        role="group"
        aria-label={uiText("{{p0}} K 線圖，可拖曳平移、滾輪縮放；方向鍵平移，加減號縮放，Home 重設", { p0: timeframe.toUpperCase() })}
        onKeyDown={keyboard}
      >
        <div className="candlestick-canvas" ref={canvasRef} />
        {!data.length && <div className="candlestick-empty" role="status">{loading ? uiText("正在取得 K 線…") : uiText("尚無 K 線資料")}</div>}{" "}
        {loading && data.length > 0 && <span className="candlestick-updating" role="status">{uiText("更新中…")}</span>}
      </div>
      <div className="candlestick-location" role="status" aria-live="polite">
        {levelsLoading ? uiText("正在計算支撐與壓力區…") : inside.length ? inside.map((zone) => (
          <span className={`candlestick-location-${isInvalidatedZone(zone) ? "invalidated" : zone.kind}`} key={zone.id}>{uiText("現價在")}{" "}{isInvalidatedZone(zone) ? uiText("已失效的原") : ""}{" "}{zoneTitle(zone)}{" "}{uiText("區") + " "}{chartPrice(zone.low)}–{chartPrice(zone.high)}</span>
        )) : zones.length ? uiText("現價在已計算的支撐與壓力區之外") : uiText("目前沒有已確認的支撐或壓力區")}
      </div>
      <div className="candlestick-help">
        <span>{uiText("拖曳平移 · 滾輪／雙指縮放 · 游標查看 K 線")}</span>
        <span>{data.length}{" " + uiText("根 K 線")}</span>
      </div>
      <a className="candlestick-attribution" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™ · © 2025 TradingView, Inc.</a>
    </div>
  );
}
