/**
 * A deliberately small markdown-to-JSX pass, for rendering the canonical
 * tutorial (`docs/writing-essays-tutorial.md`) inside the Help tab.
 *
 * It is not a general markdown engine and does not want to be: it covers
 * exactly what that document uses — headings, paragraphs, bullet and
 * numbered lists, fenced code blocks, block quotes, horizontal rules,
 * pipe tables, and the `<details>/<summary>` folds the tutorial wraps its
 * CLI track in. No dependency is added for it, and nothing is rendered
 * through `dangerouslySetInnerHTML`: every node below is built by hand,
 * so the document cannot inject markup into the app.
 */
import { Fragment, type ReactNode } from "react";

/** `**bold**`, `*italic*`, `` `code` `` and bare links, in that order. */
function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-i${index++}`;
    if (token.startsWith("`")) {
      // Code is literal: nothing inside it is markup.
      out.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      // Emphasis recurses — the tutorial routinely puts `code` inside a
      // bold lead-in ("**There is no `--force`…**").
      out.push(<strong key={key}>{inline(token.slice(2, -2), key)}</strong>);
    } else {
      out.push(<em key={key}>{inline(token.slice(1, -1), key)}</em>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Cells of a pipe-table row, without the leading/trailing pipes. */
function cells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const out: string[] = [];
  let current = "";
  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i];
    if (ch === "\\" && trimmed[i + 1] === "|") { current += "|"; i++; continue; }
    if (ch === "|") { out.push(current.trim()); current = ""; continue; }
    current += ch;
  }
  out.push(current.trim());
  return out;
}

const TABLE_RULE = /^\|?[\s:|-]+\|[\s:|-]*$/;

export function renderMarkdown(source: string): ReactNode[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let key = 0;

  const flush = () => {
    if (!paragraph.length) return;
    const text = paragraph.join(" ").trim();
    paragraph = [];
    if (text) blocks.push(<p key={`p${key++}`}>{inline(text, `p${key}`)}</p>);
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // The tutorial folds its CLI track into <details>; the tab shows those
    // sections open, with the <summary> as a small lead-in.
    if (trimmed === "<details>" || trimmed === "</details>") { flush(); continue; }
    const summary = trimmed.match(/^<summary>(.*)<\/summary>$/);
    if (summary) {
      flush();
      const inner = summary[1].replace(/<\/?b>/g, "**");
      blocks.push(<p className="doc-summary" key={`s${key++}`}>
        {inline(inner, `s${key}`)}</p>);
      continue;
    }

    if (trimmed.startsWith("```")) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        body.push(lines[i]);
        i++;
      }
      blocks.push(<pre key={`c${key++}`}><code>{body.join("\n")}</code></pre>);
      continue;
    }

    if (!trimmed) { flush(); continue; }

    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      flush();
      blocks.push(<hr key={`h${key++}`} />);
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      const Tag = `h${Math.min(level + 1, 6)}` as "h2";
      blocks.push(<Tag key={`t${key++}`}>{inline(heading[2], `t${key}`)}</Tag>);
      continue;
    }

    // A pipe table: a header row followed by a |---|---| rule.
    if (trimmed.startsWith("|") && TABLE_RULE.test(lines[i + 1]?.trim() || "")) {
      flush();
      const header = cells(trimmed);
      i += 2;
      const body: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        body.push(cells(lines[i]));
        i++;
      }
      i--;
      const tableKey = key++;
      blocks.push(
        <div className="doc-table" key={`tbl${tableKey}`}>
          <table>
            <thead><tr>{header.map((cell, c) =>
              <th key={c}>{inline(cell, `th${tableKey}-${c}`)}</th>)}</tr></thead>
            <tbody>{body.map((row, r) => <tr key={r}>{row.map((cell, c) =>
              <td key={c}>{inline(cell, `td${tableKey}-${r}-${c}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      flush();
      const body: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      i--;
      blocks.push(<blockquote key={`q${key++}`}>
        {inline(body.join(" ").trim(), `q${key}`)}</blockquote>);
      continue;
    }

    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flush();
      const ordered = !!numbered;
      const items: string[] = [];
      while (i < lines.length) {
        const raw = lines[i];
        const item = raw.trim().match(ordered ? /^\d+[.)]\s+(.*)$/ : /^[-*]\s+(.*)$/);
        if (item) { items.push(item[1]); i++; continue; }
        // A wrapped continuation line belongs to the item above it.
        if (items.length && raw.trim() && /^\s+\S/.test(raw)
            && !raw.trim().startsWith("|") && !raw.trim().startsWith("```")) {
          items[items.length - 1] += ` ${raw.trim()}`;
          i++;
          continue;
        }
        break;
      }
      i--;
      const listKey = key++;
      const children = items.map((item, n) =>
        <li key={n}>{inline(item, `li${listKey}-${n}`)}</li>);
      blocks.push(ordered
        ? <ol key={`ol${listKey}`}>{children}</ol>
        : <ul key={`ul${listKey}`}>{children}</ul>);
      continue;
    }

    paragraph.push(trimmed);
  }
  flush();
  return blocks.map((block, index) => <Fragment key={index}>{block}</Fragment>);
}
