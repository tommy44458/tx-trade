import type { ReactNode } from "react";

function safeLink(value: string) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function inline(value: string): ReactNode[] {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]+\]\([^\s)]+\)|\*[^*\n]+\*)/g;
  const nodes: ReactNode[] = [];
  let start = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > start) nodes.push(value.slice(start, match.index));
    const token = match[0];
    const key = match.index;
    if (token.startsWith("`")) nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    else if (token.startsWith("**") || token.startsWith("__")) nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith("*")) nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      const href = link && safeLink(link[2]);
      nodes.push(href ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{link![1]}</a> : token);
    }
    start = match.index + token.length;
  }
  if (start < value.length) nodes.push(value.slice(start));
  return nodes;
}

// Render common Markdown as React elements; HTML stays escaped text.
export default function DiscussionContent({ content }: { content: string }) {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const key = index;
    if (!line.trim()) { index += 1; continue; }
    if (/^\s*```/.test(line)) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
      index += 1;
      blocks.push(<pre key={key}><code>{code.join("\n")}</code></pre>);
      continue;
    }
    const heading = /^#{1,6}\s+(.+)$/.exec(line);
    if (heading) { blocks.push(<h4 key={key}>{inline(heading[1])}</h4>); index += 1; continue; }
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) { blocks.push(<hr key={key} />); index += 1; continue; }
    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={key}>{inline(quote.join("\n"))}</blockquote>);
      continue;
    }
    const list = /^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/.exec(line);
    if (list) {
      const items: ReactNode[] = [];
      const ordered = !!list[2];
      while (index < lines.length) {
        const item = /^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/.exec(lines[index]);
        if (!item || !!item[2] !== ordered) break;
        items.push(<li key={index}>{inline(item[3])}</li>);
        index += 1;
      }
      blocks.push(ordered ? <ol key={key} start={Number(list[2])}>{items}</ol> : <ul key={key}>{items}</ul>);
      continue;
    }
    if (line.includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])) {
      const cells = (row: string) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
      const headers = cells(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) rows.push(cells(lines[index++]));
      blocks.push(<div className="discussion-table" key={key}><table><thead><tr>{headers.map((cell, cellIndex) => <th key={cellIndex} scope="col">{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{headers.map((_, cellIndex) => <td key={cellIndex}>{inline(row[cellIndex] ?? "")}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^\s*(?:#{1,6}\s|```|>\s?|[-+*]\s|\d+[.)]\s)/.test(lines[index])) paragraph.push(lines[index++]);
    blocks.push(<p key={key}>{inline(paragraph.join("\n"))}</p>);
  }
  return <div className="discussion-markdown">{blocks}</div>;
}
