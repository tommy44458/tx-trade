export type IconName =
  | "market"
  | "positions"
  | "events"
  | "history"
  | "settings"
  | "refresh"
  | "arrow"
  | "close"
  | "star"
  | "search"
  | "chevronDown"
  | "plus";

const paths: Record<IconName, string> = {
  market: "M3 17l5-6 4 3 8-10M16 4h4v4M4 21h16",
  positions: "M4 7h16v13H4zM8 7V4h8v3M4 12h16M10 12v3h4v-3",
  events: "M4 6h16v14H4zM8 3v6M16 3v6M4 10h16M8 14h2M14 14h2M8 17h2",
  history: "M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M10 3h4l1 3 3 1 3 3v4l-3 3-3 1-1 3h-4l-1-3-3-1-3-3v-4l3-3 3-1z",
  refresh:
    "M20 7a9 9 0 0 0-15-2L3 7M3 3v4h4M4 17a9 9 0 0 0 15 2l2-2M21 21v-4h-4",
  arrow: "M5 12h14M14 7l5 5-5 5",
  close: "M6 6l12 12M18 6 6 18",
  star: "m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.4-5.7-3-5.7 3 1.1-6.4L2.8 9.7l6.4-.9L12 3z",
  search: "M21 21l-5-5M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0",
  chevronDown: "M6 9l6 6 6-6",
  plus: "M12 5v14M5 12h14",
};

export default function Icon({
  name,
  className = "",
}: {
  name: IconName;
  className?: string;
}) {
  return (
    <svg
      className={`icon ${className}`}
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
