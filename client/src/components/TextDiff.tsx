import { useMemo } from "react";
import { diffWords } from "diff";

type Tone = "current" | "compared";

// Renders ONE side of a diff: shows `self`, highlighting the words that are
// unique to `self` (absent from `other`). `current` = green, `compared` = red.
export function OneSidedDiff({ self, other, tone }: { self: string; other: string; tone: Tone }) {
    // diffWords(self, other): `removed` = in self but not other (self-unique).
    const parts = useMemo(() => diffWords(self, other), [self, other]);
    const hl =
        tone === "current"
            ? "bg-emerald-100 text-emerald-900 rounded-sm"
            : "bg-red-100 text-red-900 rounded-sm";

    return (
        <div className="text-sm leading-relaxed whitespace-pre-wrap text-slate-600">
            {parts
                .filter((p) => !p.added) // drop other-only words; this column shows `self`
                .map((p, i) =>
                    p.removed ? (
                        <span key={i} className={hl}>
                            {p.value}
                        </span>
                    ) : (
                        <span key={i}>{p.value}</span>
                    ),
                )}
        </div>
    );
}
