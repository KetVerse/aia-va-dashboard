FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# taipy-core pins openpyxl==3.1.2 exactly, which corrupts rich-text xlsx
# saves (used by churned_parked_tracker.py's colored Usage Streak dots).
# Installed as a SEPARATE step (not in requirements.txt) so it upgrades
# in place after taipy's own resolution, instead of conflicting with it.
RUN pip install --no-cache-dir --upgrade "openpyxl>=3.1.5"

COPY . .

EXPOSE 8080

CMD ["taipy", "run", "main.py", "--port", "8080", "--host", "0.0.0.0", "--no-reloader"]
