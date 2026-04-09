-- TerraTrust backend schema aligned with SRS v3.1 / Backend System Design v3.1

create extension if not exists postgis;
create extension if not exists postgis_topology;
create extension if not exists "uuid-ossp";

create table if not exists users (
    id uuid primary key default uuid_generate_v4(),
    firebase_uid varchar(128) unique not null,
    phone_number varchar(20) unique not null,
    full_name varchar(255),
    aadhaar_hash varchar(255),
    kyc_completed boolean default false,
    wallet_address varchar(42) unique,
    role varchar(20) default 'farmer',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists wallet_recovery_requests (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    old_wallet_address varchar(42),
    new_wallet_address varchar(42) not null,
    status varchar(20) not null default 'PENDING',
    requested_at timestamptz default now(),
    reviewed_at timestamptz,
    completed_at timestamptz,
    reviewer_note text
);

create table if not exists land_parcels (
    id uuid primary key default uuid_generate_v4(),
    user_id uuid not null references users(id) on delete cascade,
    survey_number varchar(100) not null,
    farm_name varchar(255),
    district varchar(100) not null,
    taluka varchar(100) not null,
    village varchar(100) not null,
    state varchar(100) default 'Maharashtra',
    geom geometry(multipolygon, 4326) not null,
    area_hectares decimal(10, 4),
    is_verified boolean default false,
    boundary_source varchar(20),
    ocr_owner_name varchar(255),
    doc_image_url text,
    lgd_district_code varchar(20),
    lgd_taluka_code varchar(20),
    lgd_village_code varchar(20),
    gis_code varchar(100),
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    unique (user_id, survey_number)
);

create table if not exists carbon_audits (
    id uuid primary key default uuid_generate_v4(),
    land_id uuid not null references land_parcels(id) on delete cascade,
    user_id uuid not null references users(id) on delete cascade,
    audit_year int not null,
    status varchar(20) default 'PROCESSING',
    s1_vh_mean_db decimal(10, 4),
    s1_vv_mean_db decimal(10, 4),
    s2_ndvi_mean decimal(10, 4),
    s2_evi_mean decimal(10, 4),
    gedi_height_mean decimal(10, 4),
    srtm_elevation_mean decimal(10, 4),
    srtm_slope_mean decimal(10, 4),
    nisar_used boolean default false,
    xgboost_model_version varchar(50),
    features_count int,
    total_biomass_tonnes decimal(10, 4),
    prev_year_biomass decimal(10, 4),
    delta_biomass decimal(10, 4),
    carbon_tonnes decimal(10, 4),
    co2_equivalent decimal(10, 4),
    credits_issued decimal(10, 4),
    trees_scanned_count int,
    satellite_features jsonb,
    ipfs_metadata_cid varchar(255),
    ipfs_url text,
    tx_hash varchar(66),
    token_id numeric(78, 0),
    block_number bigint,
    reason text,
    error text,
    calculated_at timestamptz,
    minted_at timestamptz,
    created_at timestamptz default now(),
    unique (land_id, audit_year)
);

create table if not exists sampling_zones (
    id uuid primary key default uuid_generate_v4(),
    land_id uuid not null references land_parcels(id) on delete cascade,
    audit_id uuid not null references carbon_audits(id) on delete cascade,
    zone_label varchar(10) not null,
    centre_point geometry(point, 4326) not null,
    radius_metres decimal(10, 2) not null,
    zone_type varchar(20),
    ndvi_mean decimal(5, 4),
    gedi_available boolean default false,
    sequence_order int,
    created_at timestamptz default now()
);

create table if not exists ar_tree_scans (
    id uuid primary key default uuid_generate_v4(),
    audit_id uuid not null references carbon_audits(id) on delete cascade,
    land_id uuid not null references land_parcels(id) on delete cascade,
    zone_id uuid references sampling_zones(id) on delete set null,
    gps_location geometry(point, 4326) not null,
    gps_accuracy_m decimal(6, 2),
    species varchar(100) not null,
    species_confidence decimal(5, 4),
    dbh_cm decimal(6, 2) not null,
    height_m decimal(6, 2),
    gedi_height_m decimal(6, 2),
    height_source varchar(20),
    wood_density decimal(4, 3) not null,
    agb_kg decimal(10, 3),
    ar_tier_used int,
    confidence_score decimal(5, 4),
    evidence_photo_path text,
    evidence_photo_hash varchar(64),
    scan_timestamp timestamptz not null,
    created_at timestamptz default now()
);

alter table if exists carbon_audits add column if not exists srtm_slope_mean decimal(10, 4);
alter table if exists carbon_audits add column if not exists features_count int;
alter table if exists carbon_audits add column if not exists trees_scanned_count int;
alter table if exists carbon_audits add column if not exists reason text;
alter table if exists sampling_zones add column if not exists gedi_available boolean default false;
alter table if exists ar_tree_scans add column if not exists species_confidence decimal(5, 4);
alter table if exists ar_tree_scans add column if not exists scan_timestamp timestamptz;

create or replace function update_land_area()
returns trigger as $$
begin
    new.area_hectares := st_area(new.geom::geography) / 10000.0;
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_update_land_area on land_parcels;
create trigger trg_update_land_area
before insert or update of geom on land_parcels
for each row
execute function update_land_area();

create index if not exists idx_users_firebase_uid on users(firebase_uid);
create index if not exists idx_users_phone on users(phone_number);
create index if not exists idx_wallet_recovery_user on wallet_recovery_requests(user_id);
create index if not exists idx_wallet_recovery_status on wallet_recovery_requests(status);
create index if not exists idx_land_parcels_user on land_parcels(user_id);
create index if not exists idx_land_parcels_geom on land_parcels using gist(geom);
create index if not exists idx_land_parcels_survey on land_parcels(survey_number);
create index if not exists idx_carbon_audits_land on carbon_audits(land_id);
create index if not exists idx_carbon_audits_user on carbon_audits(user_id);
create index if not exists idx_carbon_audits_status on carbon_audits(status);
create index if not exists idx_carbon_audits_year on carbon_audits(audit_year);
create index if not exists idx_sampling_zones_land on sampling_zones(land_id);
create index if not exists idx_sampling_zones_audit on sampling_zones(audit_id);
create index if not exists idx_sampling_zones_centre on sampling_zones using gist(centre_point);
create index if not exists idx_ar_tree_scans_audit on ar_tree_scans(audit_id);
create index if not exists idx_ar_tree_scans_land on ar_tree_scans(land_id);
create index if not exists idx_ar_tree_scans_zone on ar_tree_scans(zone_id);
create index if not exists idx_ar_tree_scans_gps on ar_tree_scans using gist(gps_location);

alter table users enable row level security;
alter table wallet_recovery_requests enable row level security;
alter table land_parcels enable row level security;
alter table carbon_audits enable row level security;
alter table sampling_zones enable row level security;
alter table ar_tree_scans enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'users'
          and policyname = 'Users can view own profile'
    ) then
        create policy "Users can view own profile" on users
            for select using (auth.uid() = id);
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'wallet_recovery_requests'
          and policyname = 'Users can view own wallet recovery requests'
    ) then
        create policy "Users can view own wallet recovery requests" on wallet_recovery_requests
            for select using (auth.uid() = user_id);
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'wallet_recovery_requests'
          and policyname = 'Users can create own wallet recovery requests'
    ) then
        create policy "Users can create own wallet recovery requests" on wallet_recovery_requests
            for insert with check (auth.uid() = user_id);
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'users'
          and policyname = 'Users can update own profile'
    ) then
        create policy "Users can update own profile" on users
            for update using (auth.uid() = id);
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'land_parcels'
          and policyname = 'Users can manage own lands'
    ) then
        create policy "Users can manage own lands" on land_parcels
            for all using (auth.uid() = user_id);
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'carbon_audits'
          and policyname = 'Users can view own audits'
    ) then
        create policy "Users can view own audits" on carbon_audits
            for select using (
                exists (
                    select 1
                    from land_parcels
                    where land_parcels.id = carbon_audits.land_id
                      and land_parcels.user_id = auth.uid()
                )
            );
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'sampling_zones'
          and policyname = 'Users can view zones for own lands'
    ) then
        create policy "Users can view zones for own lands" on sampling_zones
            for select using (
                exists (
                    select 1
                    from land_parcels
                    where land_parcels.id = sampling_zones.land_id
                      and land_parcels.user_id = auth.uid()
                )
            );
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'ar_tree_scans'
          and policyname = 'Users can view tree scans for own lands'
    ) then
        create policy "Users can view tree scans for own lands" on ar_tree_scans
            for select using (
                exists (
                    select 1
                    from land_parcels
                    where land_parcels.id = ar_tree_scans.land_id
                      and land_parcels.user_id = auth.uid()
                )
            );
    end if;
end
$$;