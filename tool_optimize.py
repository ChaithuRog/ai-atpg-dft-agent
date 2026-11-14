"""Report-driven ATPG optimization.

The agent reads each Atalanta report and decides the next run:
  * aborted faults > 0  -> Atalanta gave up on them within the backtrack limit,
    so re-run with a 10x larger limit (10 -> 100 -> 1000 -> 10000).
  * stop when no faults are aborted, when a run improves neither coverage nor
    the number of aborted faults (resolving an aborted fault as proven
    redundant also counts as progress), or when
    the largest limit has been tried.
Each run is kept; the best one is chosen by highest coverage, then fewest
patterns. Redundant faults are never "fixed" - they are proven untestable.
"""
import json
import shutil

from tool_atpg import generate_test_vectors

LIMITS = [10, 100, 1000, 10000]


def optimize_atpg(circuit_path, log=print):
    runs = []
    for b in LIMITS:
        r = json.loads(generate_test_vectors(circuit_path, backtrack_limit=b, tag=f"_b{b}"))
        if r["status"] != "success":
            return json.dumps({"status": "error", "message": r["message"], "runs": runs})
        s = r["summary"]
        run = {"backtrack_limit": b, "coverage": s.get("fault_coverage"),
               "patterns": s.get("patterns_after_compaction"),
               "redundant": s.get("redundant_faults"), "aborted": s.get("aborted_faults"),
               "test_file": r["test_vector_file"], "log_file": r["log_file"]}
        runs.append(run)
        log(f"  backtrack {b:>5}: coverage {run['coverage']:.3f} %  patterns {run['patterns']:>4}  "
            f"redundant {run['redundant']:>3}  aborted {run['aborted']:>3}")
        if not run["aborted"]:
            log("  -> no aborted faults left: stop")
            break
        if len(runs) > 1 and run["coverage"] <= runs[-2]["coverage"] \
                and run["aborted"] >= runs[-2]["aborted"]:
            log("  -> no gain in coverage or aborted faults: stop")
            break
    best = max(runs, key=lambda r: (r["coverage"], -r["aborted"], -r["patterns"]))
    base = runs[0]
    # publish the best run under the plain name so later steps use it
    final_test = best["test_file"].replace(f"_b{best['backtrack_limit']}.test", ".test")
    final_log = best["log_file"].replace(f"_b{best['backtrack_limit']}.log", ".log")
    shutil.copy(best["test_file"], final_test)
    shutil.copy(best["log_file"], final_log)
    return json.dumps({
        "status": "success", "runs": runs, "best": best,
        "baseline_coverage": base["coverage"], "best_coverage": best["coverage"],
        "coverage_gain": round(best["coverage"] - base["coverage"], 3),
        "aborted_before": base["aborted"], "aborted_after": best["aborted"],
        "redundant_before": base["redundant"], "redundant_after": best["redundant"],
        "test_vector_file": final_test, "log_file": final_log,
    })


if __name__ == "__main__":
    import sys
    print(optimize_atpg(sys.argv[1] if len(sys.argv) > 1 else "benchmarks/c432.bench"))
