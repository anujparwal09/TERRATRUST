# TerraTrust-AR Backend — Setup Guide

## Prerequisites

- **Python 3.12+**
- **Redis** (running locally or a managed instance)
- **Node.js 18+** (only for smart contract deployment)
- A **Supabase** project with PostgreSQL + PostGIS enabled
- A **Google Earth Engine** service account
- A **Pinata** account for IPFS pinning
- A **Polygon** wallet with MATIC (Amoy testnet or mainnet)

---

## 1. Create a virtual environment

```bash
python -m venv venv
```

## 2. Activate the virtual environment

**Windows:**
```bash
venv\Scripts\activate
```

**macOS / Linux:**
```bash
source venv/bin/activate
```

## 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in **all** values:

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key |
| `GEE_SERVICE_ACCOUNT_EMAIL` | GEE service account email |
| `GEE_SERVICE_ACCOUNT_KEY_PATH` | Path to GEE JSON key file |
| `ADMIN_WALLET_PRIVATE_KEY` | Polygon wallet private key |
| `ADMIN_WALLET_ADDRESS` | Polygon wallet address |
| `ALCHEMY_POLYGON_AMOY_URL` | Alchemy RPC URL (testnet) |
| `CONTRACT_ADDRESS` | Deployed TerraToken address |
| `PINATA_JWT` | Pinata JWT for IPFS |
| `REDIS_URL` | Redis connection URL |

## 5. Place the GEE service account key

Copy your Google Earth Engine service account JSON key file into the `backend/` folder:

```
backend/gee-service-account.json
```

## 6. Set up Supabase tables

Create the following tables in your Supabase SQL Editor:

```sql
-- Users
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    full_name TEXT,
    aadhaar_hash TEXT,
    phone TEXT,
    wallet_address TEXT,
    kyc_completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Land parcels
CREATE TABLE IF NOT EXISTS land_parcels (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    farm_name TEXT NOT NULL,
    survey_number TEXT NOT NULL,
    district TEXT,
    taluka TEXT,
    village TEXT,
    state TEXT,
    boundary_source TEXT,
    geojson JSONB,
    area_hectares DOUBLE PRECISION,
    status TEXT DEFAULT 'verified',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, survey_number)
);

-- Carbon audits
CREATE TABLE IF NOT EXISTS carbon_audits (
    id UUID PRIMARY KEY,
    land_id UUID REFERENCES land_parcels(id),
    user_id UUID REFERENCES users(id),
    audit_year INTEGER,
    status TEXT DEFAULT 'PROCESSING',
    zones JSONB,
    total_biomass_tonnes DOUBLE PRECISION,
    credits_issued DOUBLE PRECISION,
    delta_biomass DOUBLE PRECISION,
    carbon_tonnes DOUBLE PRECISION,
    co2_equivalent DOUBLE PRECISION,
    satellite_features JSONB,
    tx_hash TEXT,
    ipfs_url TEXT,
    block_number BIGINT,
    error TEXT,
    calculated_at TIMESTAMPTZ,
    minted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- AR tree scans
CREATE TABLE IF NOT EXISTS ar_tree_scans (
    id UUID PRIMARY KEY,
    audit_id UUID REFERENCES carbon_audits(id),
    zone_id TEXT,
    species TEXT,
    dbh_cm DOUBLE PRECISION,
    height_m DOUBLE PRECISION,
    gps JSONB,
    ar_tier_used INTEGER,
    confidence_score DOUBLE PRECISION,
    evidence_photo_hash TEXT,
    evidence_photo_path TEXT,
    wood_density DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## 7. Deploy the smart contract (optional — for minting)

```bash
cd contracts
npm init -y
npm install --save-dev @nomicfoundation/hardhat-toolbox hardhat dotenv @openzeppelin/contracts
npx hardhat compile
npx hardhat run scripts/deploy.js --network polygonAmoy
```

Copy the deployed contract address into your `.env` as `CONTRACT_ADDRESS`.

## 8. Start the API server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## 9. Start the Celery worker (separate terminal)

```bash
celery -A tasks.celery_app worker --loglevel=info
```

## 10. Verify

Open the interactive API docs:

👉 **http://localhost:8000/docs**

All endpoints should be visible and testable.

---

## API Endpoints Summary

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/auth/kyc` | Submit KYC |
| `POST` | `/api/v1/land/verify-document` | OCR land document |
| `GET` | `/api/v1/land/fetch-boundary` | Auto-fetch boundary |
| `POST` | `/api/v1/land/register` | Register land parcel |
| `GET` | `/api/v1/audit/zones` | Generate sampling zones |
| `POST` | `/api/v1/audit/submit-samples` | Submit tree scans |
| `GET` | `/api/v1/audit/result/{audit_id}` | Get audit result |
| `GET` | `/api/v1/credits/balance` | Get credit balance |
