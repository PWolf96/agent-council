"""Tiny CLI smoke-run of a deliberation (not the web entrypoint).

The web entrypoint is ``python -m server.api.app``.
"""

from dotenv import load_dotenv

from server.core.orchestration.pipeline import stream_deliberation


def main():
    load_dotenv()
    question = "Is Manchester City stronger than Arsenal this season?"

    decision = None
    for event in stream_deliberation(
        question,
        team_id="football_team_board",
        session_id="deliberation_cli",
        default_model="gpt-4o-mini",
    ):
        kind = event["event"]
        if kind == "roster":
            d = event["data"]
            print(f"ROSTER: specialists={d['admitted_specialists']} "
                  f"researchers={d['admitted_researchers']} dropped={len(d['dropped'])}")
        elif kind == "plan":
            d = event["data"]
            print(f"PLAN: {d['question_type']} | entities={d['entities']} | quant={d['quant_models']}")
        elif kind == "sufficiency":
            d = event["data"]
            print(f"SUFFICIENCY: sufficient={d['sufficient']} missing={d['missing_evidence']}")
        elif kind == "claim":
            d = event["data"]
            print(f"  CLAIM {d['claim_id']} [{d['owner']}] conf={d['confidence']} cites={d['evidence_ids']}")
        elif kind == "sweep":
            d = event["data"]
            print(f"SWEEP {d['sweep']}: admitted={d['admitted']} dropped={d['dropped']} "
                  f"revisions={d['revisions']}")
        elif kind == "crux":
            d = event["data"]
            print(f"  CRUX -> {d['action']} ({len(d['cruxes'])} pivotal): {d['reason']}")
        elif kind == "evaluation":
            d = event["data"]
            print(f"EVALUATOR: passed={d['passed']} failure={d['failure']} retries={d['retries']}")
        elif kind == "decision":
            decision = event["data"]

    if decision:
        print(f"\nDECISION (confidence {decision['confidence']}, {decision['confidence_kind']}):"
              f"\n{decision['answer']}")
        if decision.get("open_cruxes"):
            print(f"\nOpen cruxes: {len(decision['open_cruxes'])}")
        if decision["unresolved_dissent"]:
            print("\nUnresolved dissent:")
            for d in decision["unresolved_dissent"]:
                print(f"  - ({d['owner']}) {d['summary']}")


if __name__ == "__main__":
    main()
