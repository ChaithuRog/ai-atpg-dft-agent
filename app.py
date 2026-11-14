"""Web GUI for the AI-agent ATPG flow.

    streamlit run app.py

Pick an ISCAS'85/'89 benchmark (or upload a .bench), optionally upload your
own pattern file, and compare a normal Atalanta run with the report-driven
AI optimization: coverage percentages, redundant / aborted faults, the
agent's re-run history and the faults no pattern detects.
"""
import json
import os
import tempfile

import pandas as pd
import streamlit as st

from tool_atpg import find_atalanta, generate_test_vectors
from tool_fault_sim import simulate_patterns
from tool_optimize import optimize_atpg

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.join(HERE, "benchmarks")

st.set_page_config(page_title="AI-Agent ATPG", page_icon="🧪", layout="wide")
st.title("AI-Agent-Based ATPG & Fault Coverage Optimization")
st.caption("ISCAS'85 / ISCAS'89 · single stuck-at faults · Atalanta + report-driven AI optimization "
           "+ independent fault simulation")

if find_atalanta() is None:
    st.error("Atalanta was not found. Build it (see README) or set the ATALANTA_PATH environment variable.")
    st.stop()

# ---------------- inputs ----------------
with st.sidebar:
    st.header("1. Circuit")
    benches = sorted(f for f in os.listdir(BENCH_DIR) if f.endswith(".bench"))
    choice = st.selectbox("Benchmark", benches, index=benches.index("c6288.bench") if "c6288.bench" in benches else 0)
    upload = st.file_uploader("...or upload your own .bench", type=["bench"])
    st.header("2. Patterns (optional)")
    pat_upload = st.file_uploader("Your pattern file (one 0/1 string per line)", type=["txt", "test", "pat"])
    st.header("3. Mode")
    optimize = st.toggle("AI report-driven optimization", value=True,
                         help="Re-runs Atalanta with a 10x larger backtrack limit while faults are aborted, "
                              "and keeps the best run.")
    run = st.button("Run ATPG", type="primary", use_container_width=True)

if upload is not None:
    tmpdir = tempfile.mkdtemp()
    circuit = os.path.join(tmpdir, upload.name)
    with open(circuit, "wb") as fh:
        fh.write(upload.getvalue())
else:
    circuit = os.path.join(BENCH_DIR, choice)
name = os.path.splitext(os.path.basename(circuit))[0]

if not run:
    st.info("Choose a circuit in the sidebar and press **Run ATPG**.")
    st.stop()


def pct_bar(label, value):
    st.markdown(f"**{label}: {value:.3f} %**")
    st.progress(min(max(value / 100.0, 0.0), 1.0))


# ---------------- normal Atalanta ----------------
with st.spinner("Normal Atalanta run (default backtrack limit 10) ..."):
    base = json.loads(generate_test_vectors(circuit, backtrack_limit=10, tag="_normal"))
if base["status"] != "success":
    st.error(base["message"])
    st.stop()
b = base["summary"]
if base["scan_converted"]:
    st.success(f"Sequential circuit: {base['dffs_converted']} flip-flops full-scan converted "
               "(plain Atalanta rejects circuits with flip-flops).")

# ---------------- AI optimization ----------------
opt = None
if optimize:
    with st.spinner("AI agent: reading Atalanta reports and re-running ..."):
        opt = json.loads(optimize_atpg(circuit, log=lambda m: None))
    if opt["status"] != "success":
        st.error(opt["message"])
        st.stop()
best_test = opt["test_vector_file"] if opt else base["test_vector_file"]
best = opt["best"] if opt else {"coverage": b.get("fault_coverage"), "aborted": b.get("aborted_faults"),
                                "redundant": b.get("redundant_faults"),
                                "patterns": b.get("patterns_after_compaction"), "backtrack_limit": 10}

# ---------------- independent simulation ----------------
with st.spinner("Independent fault simulation ..."):
    sim_base = json.loads(simulate_patterns(circuit, base["test_vector_file"]))
    sim_best = json.loads(simulate_patterns(circuit, best_test))

# ---------------- headline metrics ----------------
st.subheader(f"Results: {name}")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Atalanta fault coverage", f"{best['coverage']:.3f} %",
          f"{best['coverage'] - b['fault_coverage']:+.3f} % vs normal" if opt else None)
c2.metric("Aborted faults", best["aborted"],
          f"{best['aborted'] - b['aborted_faults']:+d}" if opt else None, delta_color="inverse")
c3.metric("Redundant faults (proven)", best["redundant"])
c4.metric("Test patterns", best["patterns"])

left, right = st.columns(2)
with left:
    st.markdown("#### Normal Atalanta")
    pct_bar("Atalanta coverage", b["fault_coverage"])
    pct_bar("Independent simulator coverage", sim_base["fault_coverage"])
    st.caption(f"{b['patterns_after_compaction']} patterns · {b['redundant_faults']} redundant · "
               f"{b['aborted_faults']} aborted · backtrack limit 10")
with right:
    st.markdown("#### Enhanced (AI-optimized)" if opt else "#### Enhanced")
    pct_bar("Atalanta coverage", best["coverage"])
    pct_bar("Independent simulator coverage", sim_best["fault_coverage"])
    st.caption(f"{best['patterns']} patterns · {best['redundant']} redundant · {best['aborted']} aborted · "
               f"backtrack limit {best['backtrack_limit']}")

# ---------------- comparison table ----------------
st.markdown("#### Normal vs enhanced")
table = pd.DataFrame({
    "Metric": ["Atalanta coverage (%)", "Aborted faults", "Redundant faults", "Test patterns",
               "Simulator coverage (%)", "Simulator detected / total"],
    "Normal Atalanta": [f"{b['fault_coverage']:.3f}", b["aborted_faults"], b["redundant_faults"],
                        b["patterns_after_compaction"], f"{sim_base['fault_coverage']:.3f}",
                        f"{sim_base['detected']} / {sim_base['fault_universe']}"],
    "Enhanced tool": [f"{best['coverage']:.3f}", best["aborted"], best["redundant"], best["patterns"],
                      f"{sim_best['fault_coverage']:.3f}",
                      f"{sim_best['detected']} / {sim_best['fault_universe']}"],
})
st.dataframe(table, hide_index=True, use_container_width=True)

# ---------------- agent history ----------------
if opt:
    st.markdown("#### What the AI agent did")
    runs = pd.DataFrame(opt["runs"])[["backtrack_limit", "coverage", "aborted", "redundant", "patterns"]]
    runs.columns = ["Backtrack limit", "Coverage (%)", "Aborted", "Redundant", "Patterns"]
    st.dataframe(runs, hide_index=True, use_container_width=True)
    g1, g2 = st.columns(2)
    g1.markdown("Coverage per run (%)")
    g1.line_chart(runs.set_index("Backtrack limit")["Coverage (%)"])
    g2.markdown("Aborted faults per run")
    g2.bar_chart(runs.set_index("Backtrack limit")["Aborted"])
    st.caption("The agent re-runs with a 10x larger backtrack limit while faults are aborted, stops when a run "
               "improves neither coverage nor the aborted count, and keeps the best run. Redundant faults are "
               "proven untestable, so they are never 'fixed'.")

# ---------------- user pattern file ----------------
if pat_upload is not None:
    st.markdown("#### Your pattern file")
    pfile = os.path.join(tempfile.mkdtemp(), pat_upload.name)
    with open(pfile, "wb") as fh:
        fh.write(pat_upload.getvalue())
    up = json.loads(simulate_patterns(circuit, pfile))
    if up["status"] != "success":
        st.error(up["message"])
    else:
        pct_bar(f"Your {up['patterns_read']} patterns", up["fault_coverage"])
        st.caption(f"{up['detected']} of {up['fault_universe']} stuck-at faults detected; "
                   f"{up['undetected']} missed. AI-optimized Atalanta set: {sim_best['fault_coverage']:.3f} %.")
        if up["undetected"]:
            st.code("\n".join(up["undetected_sample"]))

# ---------------- undetected faults + downloads ----------------
st.markdown("#### Faults no generated pattern detects")
und = open(sim_best["undetected_file"]).read().split()
if und:
    st.write(f"{len(und)} faults (these match Atalanta's redundant + aborted faults):")
    st.code("\n".join(und[:60]) + ("\n..." if len(und) > 60 else ""))
else:
    st.write("None: every stuck-at fault is detected.")
d1, d2 = st.columns(2)
d1.download_button("Download test patterns (.test)", open(best_test).read(), f"{name}.test")
d2.download_button("Download undetected fault list", "\n".join(und), f"{name}_undetected.txt")
