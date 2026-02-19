# NYC RE Tracker

A NYC-specific residential real estate deal tracker for value-add investors. Integrates live deal ingestion from NYC Open Data, AI-ready pipeline management, and geographic visualization — all in one Streamlit app.

## What it does

- Ingests foreclosure and tax lien data from NYC Open Data
- Displays deals as filterable property cards
- Maps all deals geographically across the 5 boroughs
- Tracks deals through a personal pipeline (Watching → Analyzing → Offer Made → Dead)
- Supports manual deal entry

## Tech Stack

- **Frontend/App**: Streamlit (Python)
- **Database**: Supabase (PostgreSQL)
- **Maps**: pydeck
- **Data**: NYC Open Data API
- **Deployment**: Streamlit Community Cloud

## Setup

### Prerequisites
- Python 3.11+
- A [Supabase](https://supabase.com) project (free tier)

### Install

```bash
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Fill in SUPABASE_URL and SUPABASE_KEY
```

### Run locally

```bash
streamlit run app/main.py
```

## Project Structure

```
app/          # Streamlit app pages and views
data/         # Data ingestion scripts (NYC Open Data)
utils/        # Shared helpers (Supabase client, geocoding, etc.)
bin/          # Workflow CLI tools (vibe, ticket)
```

## Workflow Tooling

This project uses the vibe boilerplate for ticket and PR management:

```bash
bash bin/vibe setup      # Initial setup
bash bin/ticket list     # List Linear tickets
bash bin/vibe do TES-5   # Start working on a ticket
```
