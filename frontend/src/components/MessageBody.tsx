import { memo } from "react";
import type { Root, RootContent } from "mdast";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "../types";
import CitationList from "./CitationList";

const PLUGINS = [remarkGfm];

function stripCitationMarkers(citations: Citation[]) {
  const numbers = new Set(citations.map((citation) => String(citation.n)));
  const visit = (node: Root | RootContent) => {
    if (node.type === "text") {
      node.value = node.value.replace(/[ \t]*\[(\d+)\]/g, (marker, number) => numbers.has(number) ? "" : marker);
    } else if ("children" in node && node.type !== "link" && node.type !== "linkReference") {
      node.children.forEach(visit);
    }
  };
  return visit;
}

const COMPONENTS = {
  // Model output is untrusted: external links open in a new tab without access to this page.
  a: ({ node: _node, ...props }: { node?: unknown } & React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props} target="_blank" rel="noopener noreferrer nofollow" />
  ),
  // Wide tables scroll instead of stretching the page.
  table: ({ node: _node, ...props }: { node?: unknown } & React.TableHTMLAttributes<HTMLTableElement>) => (
    <div className="md-table">
      <table {...props} />
    </div>
  ),
};

/**
 * Assistant text is rendered as Markdown. Raw HTML in the text is NOT rendered (react-markdown's default),
 * and unsafe URL schemes such as javascript: are removed by its default URL transform.
 * User text is shown literally by the caller and never goes through this component.
 */
function MessageBody({ text, citations }: { text: string; citations?: Citation[] }) {
  const sources = Array.isArray(citations) && citations.length ? citations : undefined;
  return (
    <>
      <div className="md">
        <Markdown
          remarkPlugins={sources ? [remarkGfm, [stripCitationMarkers, sources]] : PLUGINS}
          components={COMPONENTS}
        >
          {text}
        </Markdown>
      </div>
      {sources && <CitationList citations={sources} />}
    </>
  );
}

export default memo(MessageBody);
