/**
 * useUploadStatus – polls GET /api/uploads/:id every 1.5s until status is
 * "done" or "failed". Stops automatically on completion or unmount.
 *
 * Also detects HTTP 410 Gone (transient TTL expired) and sets `expired = true`.
 */

import { useEffect, useRef, useState } from "react";
import { getUploadStatus, type StatusResponse } from "../lib/api";

const POLL_INTERVAL_MS = 1500;

export function useUploadStatus(uploadId: string | null) {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [loading, setLoading] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  };

  useEffect(() => {
    if (!uploadId) return;

    setLoading(true);
    setError(null);
    setExpired(false);

    const poll = async () => {
      try {
        const result = await getUploadStatus(uploadId);
        setData(result);
        setLoading(false);
        if (result.status === "done" || result.status === "failed") {
          stopPolling();
        }
      } catch (err: unknown) {
        // Detect 410 Gone = transient TTL expired
        if (err instanceof Response && err.status === 410) {
          setExpired(true);
          setLoading(false);
          stopPolling();
          return;
        }
        // Check if it's an Error with a message indicating expiry
        const msg =
          err instanceof Error ? err.message : "שגיאה בטעינת הנתונים";
        if (msg.includes("פג תוקף") || msg.includes("נמחק אוטומטית")) {
          setExpired(true);
        } else {
          setError(msg);
        }
        setLoading(false);
        stopPolling();
      }
    };

    // Immediate first fetch
    poll();

    // Then poll on interval
    intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);

    return () => stopPolling();
  }, [uploadId]);

  return { data, error, expired, loading, stopPolling };
}
