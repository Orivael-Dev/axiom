"""
AX OS — Streamlit AUI shell.
============================
The visible adaptive layer: type a goal, get a workspace that reshapes
around it (panels chosen by the planner), with the safety verdict and the
signed audit trail in view. Talks to the local AX OS service (aui.server)
over localhost — it never touches Axiom directly.

Run:
    python -m aui.server          # terminal 1 — local service on :8800
    streamlit run aui/app.py      # terminal 2 — the shell

Set AX_OS_API to point at a non-default service URL.
"""
from __future__ import annotations

import os

import requests
import streamlit as st

API = os.environ.get("AX_OS_API", "http://127.0.0.1:8800")

st.set_page_config(page_title="AX OS", layout="wide")
st.title("AX OS")
st.caption("State a goal. The workspace assembles around it — safety checked first.")

with st.sidebar:
    st.header("Intent")
    goal = st.text_input("What are you working on?",
                         "work on the launch demo branch")
    domain = st.selectbox("Domain", ["(auto)", "general", "dev", "financial",
                                     "music", "medical"])
    go = st.button("Open workspace", type="primary")


def _assemble(goal: str, domain: str) -> dict:
    payload = {"goal": goal}
    if domain != "(auto)":
        payload["domain"] = domain
    r = requests.post(f"{API}/assemble", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


if go:
    try:
        plan = _assemble(goal, domain)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not reach the AX OS service at {API}: {e}")
        st.stop()

    if not plan["allowed"]:
        st.error("Goal refused by the intent gate — no workspace assembled.")

    st.subheader(f"Workspace · scene: {plan['scene']}")
    cols = st.columns(2)
    for i, panel in enumerate(plan["panels"]):
        with cols[i % 2]:
            badge = {"ready": "🟢", "pending": "⚪", "blocked": "🔴"}.get(panel["status"], "")
            with st.container(border=True):
                st.markdown(f"**{badge} {panel['title']}**  \n*{panel['kind']}*")
                if panel["items"]:
                    for it in panel["items"]:
                        st.write(f"- {it}")
                else:
                    st.caption("workspace will gather this")

    with st.expander("Signed audit trail"):
        try:
            trail = requests.get(f"{API}/audit", params={"limit": 10}, timeout=30).json()
            ok = "✅ all verified" if trail.get("all_verified") else "🔴 tamper detected"
            st.caption(f"{trail.get('count', 0)} events · {ok}")
            for e in reversed(trail.get("events", [])):
                st.write(f"`{e.get('event_type')}` → {e.get('outcome') or '-'}")
        except Exception as e:  # noqa: BLE001
            st.caption(f"audit unavailable: {e}")

    st.caption(f"workspace signature: {plan['signature'][:24]}…")
