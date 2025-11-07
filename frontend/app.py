import streamlit as st
import requests
import json
from typing import Optional
import time

st.set_page_config(page_title="Nyay Sahayak", layout="centered")
st.title("Nyay Sahayak — Initial Action Roadmap")
st.write("Describe what happened in plain language. This tool is informational only. Be concise; avoid sharing highly sensitive PII here.")

# Input area
user_text = st.text_area("Your description", height=200)
jurisdiction = st.text_input("State / jurisdiction (optional)")

# Quick incident-type suggestions to help retrieval & classification
with st.expander("Suggest incident type (optional)"):
    incident_hint = st.selectbox("If you know the incident type, pick one (helps retrieval)", [
        "", "Cyber Fraud", "Theft", "Assault", "Hit-and-Run", "Domestic Violence", "Sexual Assault", "Property Damage", "Unknown"
    ])

# Helper: call backend with basic error handling
BACKEND_URL = "http://127.0.0.1:8000"  # prefer loopback; ensure backend is started with uvicorn

# New: clear start instructions shown when backend not reachable
START_INSTRUCTIONS = (
    "Start the backend (Windows CMD):\n"
    "  .venv\\Scripts\\Activate\n"
    "  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000\n\n"
    "PowerShell (from project root):\n"
    "  . .venv\\Scripts\\Activate.ps1\n"
    "  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000\n\n"
    "Or run the helper scripts in project root:\n"
    "  start_backend.bat   (cmd)\n"
    "  start_backend.ps1   (PowerShell)\n\n"
    "If problems persist: ensure you created the virtualenv named '.venv' and installed requirements, then check backend logs/terminal for errors."
)

def check_backend(timeout=2):
    """
    Return health JSON dict when backend reachable, otherwise None.
    Adds a _latency field (seconds) to help detect slow networks.
    """
    try:
        resp = requests.get(BACKEND_URL.rstrip("/") + "/health", timeout=timeout)
        if resp.status_code == 200:
            try:
                h = resp.json()
            except Exception:
                h = {"status": "ok", "raw_text": resp.text}
            # attach measured latency (best-effort)
            try:
                h["_latency"] = float(resp.elapsed.total_seconds())
            except Exception:
                h["_latency"] = None
            return h
    except requests.exceptions.RequestException:
        return None
    return None

def wait_for_health(timeout: int = 30, interval: int = 2) -> Optional[dict]:
    """
    Poll /health until it responds or timeout (seconds) expires.
    Returns the health JSON if successful, otherwise None.
    """
    start = time.time()
    with st.spinner("Waiting for backend to become reachable..."):
        while time.time() - start < timeout:
            try:
                r = requests.get(BACKEND_URL.rstrip("/") + "/health", timeout=5)
                if r.status_code == 200:
                    try:
                        h = r.json()
                    except Exception:
                        h = {"status": "ok", "raw_text": r.text}
                    # attach latency for diagnostics
                    try:
                        h["_latency"] = float(r.elapsed.total_seconds())
                    except Exception:
                        h["_latency"] = None
                    st.success("Backend reachable.")
                    return h
                else:
                    st.info(f"/health returned {r.status_code}; retrying...")
            except requests.exceptions.RequestException:
                st.info("Backend not reachable yet; retrying...")
            time.sleep(interval)
    return None

def wait_for_ready(timeout: int = 120, interval: int = 5) -> bool:
    """
    Poll /ready until it reports ready or timeout (seconds) expires.
    Updates the Streamlit UI while waiting.
    """
    start = time.time()
    with st.spinner("Waiting for backend to finish loading resources..."):
        while time.time() - start < timeout:
            try:
                r = requests.get(BACKEND_URL.rstrip("/") + "/ready", timeout=5)
                if r.status_code == 200:
                    j = r.json()
                    if j.get("ready"):
                        st.success("Backend resources ready.")
                        return True
                    # show brief status line (non-intrusive)
                    msg = j.get("message") or j.get("error") or "Resources still loading..."
                    st.info(msg, icon="⏳")
                else:
                    st.warning(f"/ready returned {r.status_code}; retrying...")
            except requests.exceptions.RequestException as e:
                st.warning(f"Ready-check failed: {e}; retrying...")
            time.sleep(interval)
    st.error("Timed out waiting for backend readiness. See start instructions below.")
    return False

def call_backend(path: str, payload: dict, timeout=30, token: Optional[str]=None) -> Optional[dict]:
    # ensure backend is reachable before calling
    health = check_backend()
    if not health:
        # try to wait briefly for backend to appear before showing manual instructions
        health = wait_for_health(timeout=30, interval=2)
        if not health:
            # Show a helpful, copy-ready message with exact commands
            st.error("Cannot reach backend at " + BACKEND_URL + ". See instructions below to start it.")
            with st.expander("How to start the backend (click to expand)"):
                st.code(START_INSTRUCTIONS)
            return None
        # continue with the health JSON we obtained

    # show slow-network notice if backend responded but latency is high
    latency = health.get("_latency")
    if latency and latency > 1.5:
        st.warning(f"Detected slow backend/network (latency {latency:.1f}s). This may delay generation and cause font fallbacks in the browser.", icon="⏳")

    # If backend is up but resources are still loading or errored, surface that to user
    resources_loaded = health.get("resources_loaded", False)
    resource_error = health.get("resource_error")
    if not resources_loaded:
        st.warning("Backend reachable but resources not ready. Attempting to wait for readiness...", icon="⏳")
        if resource_error:
            st.error("Resource load error (see backend logs): " + str(resource_error))
        with st.expander("Backend health (click to expand)"):
            st.json(health)
        # Auto-wait for readiness; if ready, continue and perform the request automatically
        ready = wait_for_ready(timeout=180, interval=5)
        if not ready:
            with st.expander("How to start / troubleshoot the backend"):
                st.code(START_INSTRUCTIONS)
            return None
        # re-check health after successful wait
        health = check_backend()
        if not health or not health.get("resources_loaded", False):
            st.error("Backend reported ready but health check still indicates resources not loaded.")
            return None

    # proceed with normal query call (perform POST once, after any waits)
    try:
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        resp = requests.post(BACKEND_URL.rstrip("/") + path, json=payload, headers=headers or None, timeout=timeout)
        # Prefer to show backend JSON even on non-200 so user sees detailed error
        try:
            j = resp.json()
        except Exception:
            j = None

        if resp.status_code != 200:
            # show backend provided JSON error if available, otherwise raw text
            if j:
                st.error(f"Backend error ({resp.status_code}): {json.dumps(j, indent=2, ensure_ascii=False)}")
            else:
                st.error(f"Backend error ({resp.status_code}): {resp.text}")
            return None

        # status 200: check if backend returned an error payload inside JSON
        if isinstance(j, dict) and j.get("error"):
            st.error("Backend returned an error: " + str(j.get("error")))
            # show full payload for debugging
            with st.expander("Backend response (debug)"):
                st.json(j)
            return None

        return j
    except requests.exceptions.RequestException as e:
        st.error(f"Backend request failed: {e}\nMake sure the backend is running and reachable at {BACKEND_URL}")
    return None

# Render structured roadmap if available
def render_roadmap(response: dict):
    st.subheader("Initial Action Roadmap")
    # Prefer structured fields if backend provides them, otherwise show raw answer
    if isinstance(response, dict) and any(k in response for k in ("classification","immediate_actions","fir_steps","evidence","legal_sections")):
        cls = response.get("classification", "Unknown")
        conf = response.get("confidence", "Low")
        st.markdown(f"**Incident classification:** {cls} — *{conf}*")
        st.markdown("**Reason / sources:** " + response.get("reason","Not found in sources"))

        st.markdown("### Immediate actions")
        for it in response.get("immediate_actions", []):
            st.write("- " + it.get("text", it) + (f"  [{', '.join(it.get('sources',[]))}]" if isinstance(it, dict) else ""))

        st.markdown("### How to file FIR")
        for step in response.get("fir_steps", []):
            st.write("- " + (step.get("text", step) if isinstance(step, dict) else step) + (f"  [{', '.join(step.get('sources',[]))}]" if isinstance(step, dict) else ""))

        st.markdown("### Evidence to preserve")
        for ev in response.get("evidence", []):
            st.write("- " + (ev.get("text", ev) if isinstance(ev, dict) else ev) + (f"  [{', '.join(ev.get('sources',[]))}]" if isinstance(ev, dict) else ""))

        st.markdown("### Likely legal sections")
        for sec in response.get("legal_sections", []):
            s_text = sec if isinstance(sec, str) else f"{sec.get('section')} — {sec.get('rationale','')}"
            s_src = "" if isinstance(sec, str) else f" [{', '.join(sec.get('sources',[]))}]"
            st.write(f"- {s_text}{s_src}")

        # FIR draft & timeline if present
        if response.get("draft_fir"):
            with st.expander("View FIR draft"):
                # use a text area instead of st.code to avoid heavy monospace font loads
                st.text_area("FIR draft (copy/save as needed)", response["draft_fir"], height=300)
                st.download_button("Download FIR draft", response["draft_fir"], file_name="fir_draft.txt")
        if response.get("timeline"):
            with st.expander("Evidence timeline"):
                # timeline might be long; avoid st.code to reduce font fetches
                st.text_area("Evidence timeline", response["timeline"], height=200)
                st.download_button("Download timeline", response["timeline"], file_name="evidence_timeline.txt")

        # Show citations mapped per sentence if provided
        if response.get("citations"):
            st.markdown("### Citations (per sentence)")
            for c in response["citations"]:
                if isinstance(c, dict):
                    st.write(f"- {c.get('sentence')}")
                    st.write("    → ", c.get("citation"))
                else:
                    st.write("- " + str(c))

        # Used passages list
        if response.get("used_passages"):
            st.markdown("### Sources used")
            for p in response["used_passages"]:
                meta = p.get("meta", {})
                st.write(f"* {p.get('id')} — {meta.get('source_name','')} — {meta.get('url','')}")
    else:
        # fallback: raw LLM answer
        st.markdown("### Raw answer")
        # avoid rendering large code blocks with st.code to prevent browser font fetches;
        # use markdown / text_area depending on length
        ans = response.get("answer","No answer returned.")
        if isinstance(ans, str) and len(ans) > 800:
            st.text_area("Answer (long)", ans, height=300)
        else:
            st.markdown(ans)

    st.info("This tool provides information, not legal advice. For urgent/legal representation contact a licensed lawyer or your local police. Nyay Sahayak is not a substitute for professional legal counsel.")

# Button handlers
if st.button("Generate Roadmap"):
    if not user_text.strip():
        st.warning("Please enter a description")
    else:
        payload = {"user_text": user_text, "jurisdiction": jurisdiction or None, "incident_hint": incident_hint or None}
        with st.spinner("Generating..."):
            res = call_backend("/query", payload)
        if res:
            st.session_state["last_response"] = res
            render_roadmap(res)

# If we already have a response in session, show quick actions
if st.session_state.get("last_response"):
    st.markdown("---")
    st.subheader("Quick actions")
    last = st.session_state["last_response"]
    # Download full roadmap text (prefer structured 'answer' or compose)
    def compose_full_text(resp):
        if isinstance(resp, dict) and resp.get("answer"):
            return resp["answer"]
        # else create a minimal text from structured fields
        parts = []
        parts.append(f"Classification: {resp.get('classification','Unknown')} ({resp.get('confidence','')})\n")
        parts.append("Immediate actions:\n")
        for a in resp.get("immediate_actions", []):
            parts.append("- " + (a.get("text", a) if isinstance(a, dict) else a) + "\n")
        parts.append("\nFIR steps:\n")
        for s in resp.get("fir_steps", []):
            parts.append("- " + (s.get("text", s) if isinstance(s, dict) else s) + "\n")
        parts.append("\nEvidence:\n")
        for e in resp.get("evidence", []):
            parts.append("- " + (e.get("text", e) if isinstance(e, dict) else e) + "\n")
        return "".join(parts)

    full_text = compose_full_text(last)
    st.download_button("Download full Roadmap", full_text, file_name="nyay_sahayak_roadmap.txt")

    # Email send (calls backend /send_email; backend must implement)
    with st.form("email_form"):
        st.write("Email this roadmap")
        to_email = st.text_input("Recipient email")
        subject = st.text_input("Subject", value="Nyay Sahayak — Initial Action Roadmap")
        send_btn = st.form_submit_button("Send Email")
        if send_btn:
            if not to_email:
                st.warning("Enter recipient email")
            else:
                payload = {"to": to_email, "subject": subject, "body": full_text}
                with st.spinner("Sending email..."):
                    resp = call_backend("/send_email", payload)
                if resp:
                    st.success("Email send request submitted. Backend response: " + json.dumps(resp))

    # Option to request a FIR draft (if not present, ask backend to generate)
    if not last.get("draft_fir"):
        if st.button("Generate FIR draft"):
            reporter = st.session_state.get("user_profile")
            payload = {"user_text": user_text, "context": last.get("used_passages"), "reporter_info": reporter}
            with st.spinner("Generating FIR draft..."):
                resp = call_backend("/generate_fir", payload, token=st.session_state.get("auth_token"))
            if resp and resp.get("draft_fir"):
                st.session_state["last_response"]["draft_fir"] = resp["draft_fir"]
                st.success("FIR draft generated")
                st.experimental_rerun()

# Add: centralized auth page (signup / login) shown before main UI
def render_auth():
    st.title("Nyay Sahayak — Sign in / Sign up")
    st.write("Create an account or sign in to pre-fill reporter details when generating an FIR draft.")
    cols = st.columns(2)
    with cols[0]:
        st.subheader("Sign up")
        s_name = st.text_input("Full name", key="auth_su_name")
        s_dob = st.text_input("Date of birth (YYYY-MM-DD)", key="auth_su_dob")
        s_address = st.text_area("Address", key="auth_su_address")
        s_email = st.text_input("Email", key="auth_su_email")
        s_pass = st.text_input("Password", type="password", key="auth_su_pass")
        if st.button("Create account"):
            if not (s_name and s_dob and s_address and s_email and s_pass):
                st.warning("Fill all fields")
            else:
                with st.spinner("Creating account..."):
                    res = call_backend("/signup", {"name": s_name, "dob": s_dob, "address": s_address, "email": s_email, "password": s_pass})
                if res and res.get("status") == "ok":
                    st.success("Account created. Redirecting to main page...")
                    st.session_state["auth_token"] = res["token"]
                    st.session_state["user_profile"] = res["user"]
                    st.experimental_rerun()
                else:
                    st.error("Signup failed: " + json.dumps(res))

    with cols[1]:
        st.subheader("Login")
        l_email = st.text_input("Email", key="auth_li_email")
        l_pass = st.text_input("Password", type="password", key="auth_li_pass")
        if st.button("Login"):
            if not (l_email and l_pass):
                st.warning("Enter email and password")
            else:
                with st.spinner("Logging in..."):
                    res = call_backend("/login", {"email": l_email, "password": l_pass})
                if res and res.get("status") == "ok":
                    st.success("Logged in — redirecting to main page...")
                    st.session_state["auth_token"] = res["token"]
                    st.session_state["user_profile"] = res["user"]
                    st.experimental_rerun()
                else:
                    st.error("Login failed: " + json.dumps(res))

    st.markdown("---")
    st.write("Or continue without signing in (reporter info won't be pre-filled).")
    if st.button("Continue without signing in"):
        st.session_state["auth_token"] = None
        st.session_state["user_profile"] = None
        st.experimental_rerun()

# Wrap the existing main UI into a function so we can call it after auth completes.
def render_main():
    # Render structured roadmap if available
    def render_roadmap(response: dict):
        st.subheader("Initial Action Roadmap")
        # Prefer structured fields if backend provides them, otherwise show raw answer
        if isinstance(response, dict) and any(k in response for k in ("classification","immediate_actions","fir_steps","evidence","legal_sections")):
            cls = response.get("classification", "Unknown")
            conf = response.get("confidence", "Low")
            st.markdown(f"**Incident classification:** {cls} — *{conf}*")
            st.markdown("**Reason / sources:** " + response.get("reason","Not found in sources"))

            st.markdown("### Immediate actions")
            for it in response.get("immediate_actions", []):
                st.write("- " + it.get("text", it) + (f"  [{', '.join(it.get('sources',[]))}]" if isinstance(it, dict) else ""))

            st.markdown("### How to file FIR")
            for step in response.get("fir_steps", []):
                st.write("- " + (step.get("text", step) if isinstance(step, dict) else step) + (f"  [{', '.join(step.get('sources',[]))}]" if isinstance(step, dict) else ""))

            st.markdown("### Evidence to preserve")
            for ev in response.get("evidence", []):
                st.write("- " + (ev.get("text", ev) if isinstance(ev, dict) else ev) + (f"  [{', '.join(ev.get('sources',[]))}]" if isinstance(ev, dict) else ""))

            st.markdown("### Likely legal sections")
            for sec in response.get("legal_sections", []):
                s_text = sec if isinstance(sec, str) else f"{sec.get('section')} — {sec.get('rationale','')}"
                s_src = "" if isinstance(sec, str) else f" [{', '.join(sec.get('sources',[]))}]"
                st.write(f"- {s_text}{s_src}")

            # FIR draft & timeline if present
            if response.get("draft_fir"):
                with st.expander("View FIR draft"):
                    # use a text area instead of st.code to avoid heavy monospace font loads
                    st.text_area("FIR draft (copy/save as needed)", response["draft_fir"], height=300)
                    st.download_button("Download FIR draft", response["draft_fir"], file_name="fir_draft.txt")
            if response.get("timeline"):
                with st.expander("Evidence timeline"):
                    # timeline might be long; avoid st.code to reduce font fetches
                    st.text_area("Evidence timeline", response["timeline"], height=200)
                    st.download_button("Download timeline", response["timeline"], file_name="evidence_timeline.txt")

            # Show citations mapped per sentence if provided
            if response.get("citations"):
                st.markdown("### Citations (per sentence)")
                for c in response["citations"]:
                    if isinstance(c, dict):
                        st.write(f"- {c.get('sentence')}")
                        st.write("    → ", c.get("citation"))
                    else:
                        st.write("- " + str(c))

            # Used passages list
            if response.get("used_passages"):
                st.markdown("### Sources used")
                for p in response["used_passages"]:
                    meta = p.get("meta", {})
                    st.write(f"* {p.get('id')} — {meta.get('source_name','')} — {meta.get('url','')}")
        else:
            # fallback: raw LLM answer
            st.markdown("### Raw answer")
            # avoid rendering large code blocks with st.code to prevent browser font fetches;
            # use markdown / text_area depending on length
            ans = response.get("answer","No answer returned.")
            if isinstance(ans, str) and len(ans) > 800:
                st.text_area("Answer (long)", ans, height=300)
            else:
                st.markdown(ans)

        st.info("This tool provides information, not legal advice. For urgent/legal representation contact a licensed lawyer or your local police. Nyay Sahayak is not a substitute for professional legal counsel.")

    # Button handlers
    if st.button("Generate Roadmap"):
        if not user_text.strip():
            st.warning("Please enter a description")
        else:
            payload = {"user_text": user_text, "jurisdiction": jurisdiction or None, "incident_hint": incident_hint or None}
            with st.spinner("Generating..."):
                res = call_backend("/query", payload)
            if res:
                st.session_state["last_response"] = res
                render_roadmap(res)

    # If we already have a response in session, show quick actions
    if st.session_state.get("last_response"):
        st.markdown("---")
        st.subheader("Quick actions")
        last = st.session_state["last_response"]
        # Download full roadmap text (prefer structured 'answer' or compose)
        def compose_full_text(resp):
            if isinstance(resp, dict) and resp.get("answer"):
                return resp["answer"]
            # else create a minimal text from structured fields
            parts = []
            parts.append(f"Classification: {resp.get('classification','Unknown')} ({resp.get('confidence','')})\n")
            parts.append("Immediate actions:\n")
            for a in resp.get("immediate_actions", []):
                parts.append("- " + (a.get("text", a) if isinstance(a, dict) else a) + "\n")
            parts.append("\nFIR steps:\n")
            for s in resp.get("fir_steps", []):
                parts.append("- " + (s.get("text", s) if isinstance(s, dict) else s) + "\n")
            parts.append("\nEvidence:\n")
            for e in resp.get("evidence", []):
                parts.append("- " + (e.get("text", e) if isinstance(e, dict) else e) + "\n")
            return "".join(parts)

        full_text = compose_full_text(last)
        st.download_button("Download full Roadmap", full_text, file_name="nyay_sahayak_roadmap.txt")

        # Email send (calls backend /send_email; backend must implement)
        with st.form("email_form"):
            st.write("Email this roadmap")
            to_email = st.text_input("Recipient email")
            subject = st.text_input("Subject", value="Nyay Sahayak — Initial Action Roadmap")
            send_btn = st.form_submit_button("Send Email")
            if send_btn:
                if not to_email:
                    st.warning("Enter recipient email")
                else:
                    payload = {"to": to_email, "subject": subject, "body": full_text}
                    with st.spinner("Sending email..."):
                        resp = call_backend("/send_email", payload)
                    if resp:
                        st.success("Email send request submitted. Backend response: " + json.dumps(resp))

        # Option to request a FIR draft (if not present, ask backend to generate)
        if not last.get("draft_fir"):
            if st.button("Generate FIR draft"):
                reporter = st.session_state.get("user_profile")
                payload = {"user_text": user_text, "context": last.get("used_passages"), "reporter_info": reporter}
                with st.spinner("Generating FIR draft..."):
                    resp = call_backend("/generate_fir", payload, token=st.session_state.get("auth_token"))
                if resp and resp.get("draft_fir"):
                    st.session_state["last_response"]["draft_fir"] = resp["draft_fir"]
                    st.success("FIR draft generated")
                    st.experimental_rerun()

# Replace sidebar auth UI: the previous sidebar auth block is removed (we render auth page centrally).
# At top-level decide which page to show:
if __name__ == "__main__" and False:
    # Quick run instructions when executed directly with `python frontend/app.py`
    print("Nyay Sahayak — frontend helper")
    print("1) Ensure backend is running: uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000")
    print("2) Start UI with Streamlit (recommended): streamlit run frontend/app.py")
    print("3) If running directly for debugging: python frontend/app.py (this just prints these instructions).")
    pass

# Top-level routing: show auth first unless user already has a token/user_profile or chose to continue.
if not st.session_state.get("auth_token") and not st.session_state.get("user_profile"):
    render_auth()
else:
    render_main()
