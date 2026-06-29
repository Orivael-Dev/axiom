# AXIOM Studio — the full Streamlit ui.py (deployed at lab.orivael.dev)
#
# Build from the REPO ROOT of a checkout with the full ui.py + the RAG modules:
#   docker build -f deploy/studio/Dockerfile -t axiom-studio:local .
#
# IMPORTANT: rebuild with --no-cache when you change the pip line — the base image's
# pip layer can be served stale, silently skipping a newly-added dep (e.g. `rich`):
#   docker build --no-cache -f deploy/studio/Dockerfile -t axiom-studio:local .
#
# The base image ships axiom_constitutional, axiom_files, axiom_exoskeleton*, axiom_medical_*.
# The modules below were added to the repo after the base image was built, so they are
# layered in here; pandas/plotly/streamlit/rich are pip-installed.
FROM orivael/axiom-research:local

COPY axiom_audio /app/axiom_audio
COPY axiom_dev_loop.py axiom_twitter_agent.py axiom_twitter_agent_ledger.py ui.py /app/
# Experience-RAG prompt memory + Knowledge-RAG (new modules inside axiom_constitutional)
COPY axiom_constitutional/prompt_memory.py axiom_constitutional/knowledge_rag.py /app/axiom_constitutional/

# rich is a core dep of axiom_constitutional/evolution.py + meta_evolution.py.
RUN pip install --no-cache-dir streamlit pandas plotly rich

# The base image runs as non-root user `axiom` (uid 999). The runtime persists
# prompt memory + evolution logs under /persist, so /persist must be writable by
# that user. A fresh named volume mounted here inherits this ownership on first
# mount; without it the app hits PermissionError on LOGS_DIR.mkdir / prompt_memory.
RUN mkdir -p /persist && chown -R 999:999 /persist

EXPOSE 8501
ENTRYPOINT []
# python3 -m streamlit (the `streamlit` console script is not on PATH in the base image)
CMD ["python3","-m","streamlit","run","/app/ui.py", \
     "--server.port=8501","--server.address=0.0.0.0","--server.headless=true", \
     "--server.enableCORS=false","--server.enableXsrfProtection=false", \
     "--browser.gatherUsageStats=false"]
