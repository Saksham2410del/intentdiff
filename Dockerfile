FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The Berka data isn't in the repo, so the image downloads it and builds the
# database once at build time (~90 MB). The raw files aren't needed after that.
RUN python scripts/download_data.py && python scripts/build_db.py && rm -rf data/raw

# Fetch the embedding model now rather than on the first request
RUN python -c "from intentdiff.history import TicketHistory; TicketHistory.load()"

# Render and most other hosts set PORT. Set GEMINI_API_KEY when you run the container.
ENV PORT=8000
EXPOSE 8000
CMD uvicorn app.server:app --host 0.0.0.0 --port $PORT
