import { useEffect, useState } from "react";
import type { TimelinePost } from "../lib/api";

/**
 * Subscribe to /api/events (SSE). Pass `sinceId` to seed the stream — the
 * server only emits post events with id > sinceId. Returns the rolling
 * list of posts received this session, newest-first.
 *
 * EventSource handles reconnection automatically. We don't track that
 * explicitly; on reconnect the server resumes from `since_id`, the client
 * just keeps appending.
 */
export function useLivePosts(sinceId: number | null): TimelinePost[] {
  const [posts, setPosts] = useState<TimelinePost[]>([]);

  useEffect(() => {
    if (sinceId === null) return;
    const url = `/api/events?since_id=${sinceId}`;
    const es = new EventSource(url);

    const onPost = (ev: MessageEvent) => {
      try {
        const p = JSON.parse(ev.data) as TimelinePost;
        if ((p as { missing?: boolean }).missing) return;
        setPosts((prev) => {
          if (prev.some((x) => x.id === p.id)) return prev;
          return [p, ...prev].slice(0, 200);
        });
      } catch {
        /* ignore malformed frame */
      }
    };
    es.addEventListener("post", onPost);

    return () => {
      es.removeEventListener("post", onPost);
      es.close();
    };
  }, [sinceId]);

  return posts;
}
