import { useEffect, useState } from "react";
import { RunCard } from "@/components/RunCard";
import { NewRunModal } from "@/components/NewRunModal";
import { useRunFeed } from "@/hooks/useRunFeed";
import { fetchTeams, deleteRun } from "@/lib/api";
import type { Team } from "@/types";

interface RunFeedProps {
    onOpenDeliberation: (id: string) => void;
    onOpenStats: (id: string) => void;
}

export function RunFeed({ onOpenDeliberation, onOpenStats }: RunFeedProps) {
    const { runs, error, loading, refresh } = useRunFeed(3000);
    const [teams, setTeams] = useState<Team[]>([]);
    const [modalOpen, setModalOpen] = useState(false);

    useEffect(() => {
        fetchTeams().then(setTeams).catch(() => setTeams([]));
    }, []);

    async function handleDelete(id: string) {
        await deleteRun(id);
        refresh();
    }

    return (
        <div className="h-full overflow-y-auto">
            <div className="max-w-3xl mx-auto px-6 py-6">
                <div className="flex items-center justify-between mb-5">
                    <div>
                        <h2 className="text-base font-bold text-slate-800">Deliberations</h2>
                        <p className="text-[11px] text-slate-400">
                            {runs.length} session{runs.length === 1 ? "" : "s"} · auto-refreshing
                        </p>
                    </div>
                </div>

                {error && (
                    <div className="text-[11px] text-red-500 mb-3">Could not reach backend: {error}</div>
                )}

                {!loading && runs.length === 0 && (
                    <div className="text-center py-20 text-slate-400">
                        <p className="text-sm">No deliberations yet.</p>
                        <p className="text-[11px] mt-1">Click the + button to start one.</p>
                    </div>
                )}

                <div className="flex flex-col gap-4">
                    {runs.map((run) => (
                        <RunCard
                            key={run.id}
                            run={run}
                            onOpenDeliberation={onOpenDeliberation}
                            onOpenStats={onOpenStats}
                            onDelete={handleDelete}
                        />
                    ))}
                </div>
            </div>

            <button
                onClick={() => setModalOpen(true)}
                title="New deliberation"
                className="fixed bottom-8 right-8 w-14 h-14 rounded-full bg-indigo-600 hover:bg-indigo-700 text-white text-3xl leading-none shadow-lg flex items-center justify-center transition-colors"
            >
                +
            </button>

            {modalOpen && (
                <NewRunModal
                    teams={teams}
                    onClose={() => setModalOpen(false)}
                    onCreated={() => {
                        setModalOpen(false);
                        refresh();
                    }}
                />
            )}
        </div>
    );
}
