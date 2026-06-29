# AXIOM Studio — the full Streamlit ui.py (deployed at lab.orivael.dev)
#
# Build from the REPO ROOT of a checkout that contains the full ui.py + prompt_memory:
#   docker build -f deploy/studio/Dockerfile -t axiom-studio:local .
#
# The base image ships axiom_constitutional, axiom_files, axiom_exoskeleton*, axiom_medical_*.
# The modules below were added to the repo after the base image was built, so they are
# layered in here; pandas/plotly/streamlit are pip-installed.
FROM orivael/axiom-research:local

COPY axiom_audio /app/axiom_audio
COPY axiom_dev_loop.py axiom_twitter_agent.py axiom_twitter_agent_ledger.py ui.py /app/
# Experience-RAG prompt memory (new module inside axiom_constitutional)
COPY axiom_constitutional/prompt_memory.py /app/axiom_constitutional/prompt_memory.py

RUN pip install --no-cache-dir streamlit pandas plotly

EXPOSE 8501
ENTRYPOINT []
# python3 -m streamlit (the `streamlit` console script is not on PATH in the base image)
CMD ["python3","-m","streamlit","run","/app/ui.py", \
     "--server.port=8501","--server.address=0.0.0.0","--server.headless=true", \
     "--server.enableCORS=false","--server.enableXsrfProtection=false", \
     "--browser.gatherUsageStats=false"]
