/**
 * useUploadStatus – polls GET /api/uploads/:id every 1.5s until status is
 * "done" or "failed". Stops automatically on completion or unmount.
 */

import { useEffect, useRef, useState } from "react";
import { getUploadStatus, type StatusResponse } from "../lib/api";

const POLL_INTERVAL_MS = 1500;

export function useUploadStatus(uploadId: string | null) {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
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

    const poll = async () => {
      try {
        const result = await getUploadStatus(uploadId);
        setData(result);
        setLoading(false);
        if (result.status === "done" || result.status === "failed") {
          stopPolling();
        }
      } catch (err: unknown) {
        const msg =
          err instanceof Error ? err.message : "שגיאה בטעינת הנתונים";
        setError(msg);
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

  return { data, error, loading, stopPolling };
}
