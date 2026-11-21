# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image we labeled every commit in: its Python and SQLite
# decide which sqlglot tests run, so keep it if your image runs them.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY tools/fetch_source.py /app/fetch_source.py
RUN python /app/fetch_source.py /opt/sqlglot
COPY selector.py /app/selector.py
COPY semantic_values.py /app/semantic_values.py
COPY maps/dependencies.json /app/maps/dependencies.json
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "/app/selector.py"]
