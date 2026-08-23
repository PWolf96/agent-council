import { useCallback, useEffect, useRef, useState } from "react";
import { fetchRuns } from "@/lib/api";
import type { RunSummary } from "@/types";

// Polls the run feed every `intervalMs`. Pauses while the tab is hidden and
// refetches immediately when it becomes visible again.
export function useRunFeed(intervalMs = 3000) {
    const [runs, setRuns] = useState<RunSummary[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const timer = useRef<number | null>(null);

    const refresh = useCallback(async () => {
        try {
            const data = await fetchRuns();
            setRuns(data);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load runs");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        refresh();
        timer.current = window.setInterval(() => {
            if (!document.hidden) refresh();
        }, intervalMs);

        const onVisible = () => {
            if (!document.hidden) refresh();
        };
        document.addEventListener("visibilitychange", onVisible);

        return () => {
            if (timer.current) window.clearInterval(timer.current);
            document.removeEventListener("visibilitychange", onVisible);
        };
    }, [refresh, intervalMs]);

    return { runs, error, loading, refresh };
}
