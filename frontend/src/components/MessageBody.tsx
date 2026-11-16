import { memo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

const PLUGINS = [remarkGfm];
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
function MessageBody({ text }: { text: string }) {
  return (
    <div className="md">
      <Markdown remarkPlugins={PLUGINS} components={COMPONENTS}>
        {text}
      </Markdown>
    </div>
  );
}

export default memo(MessageBody);
