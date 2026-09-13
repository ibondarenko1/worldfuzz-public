-- M1 immutable versioned entities, blobs and dataset generations.
BEGIN;
CREATE TABLE worldfuzz.versioned_entities (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    version TEXT NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (entity_type, entity_id, version)
);
CREATE TABLE worldfuzz.blob_objects (
    sha256 TEXT PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    storage_key TEXT NOT NULL UNIQUE,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE worldfuzz.split_generation_groups (
    split_type TEXT NOT NULL DEFAULT 'SplitManifest' CHECK (split_type = 'SplitManifest'),
    split_id TEXT NOT NULL,
    split_version TEXT NOT NULL,
    group_id TEXT NOT NULL,
    split TEXT NOT NULL CHECK (split IN ('train','validation','test','excluded')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (split_id, split_version, group_id),
    UNIQUE (split_id, split_version, ordinal),
    FOREIGN KEY (split_type, split_id, split_version)
        REFERENCES worldfuzz.versioned_entities(entity_type, entity_id, version)
);
CREATE TABLE worldfuzz.dataset_generation_members (
    dataset_type TEXT NOT NULL DEFAULT 'DatasetManifest' CHECK (dataset_type = 'DatasetManifest'),
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    episode_type TEXT NOT NULL DEFAULT 'Episode' CHECK (episode_type = 'Episode'),
    episode_id TEXT NOT NULL,
    episode_version TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (dataset_id, dataset_version, episode_id, episode_version),
    UNIQUE (dataset_id, dataset_version, ordinal),
    FOREIGN KEY (dataset_type, dataset_id, dataset_version)
        REFERENCES worldfuzz.versioned_entities(entity_type, entity_id, version),
    FOREIGN KEY (episode_type, episode_id, episode_version)
        REFERENCES worldfuzz.versioned_entities(entity_type, entity_id, version)
);

CREATE FUNCTION worldfuzz.reject_versioned_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'versioned M1 catalog records are immutable';
END;
$$;
CREATE TRIGGER immutable_versioned_entities BEFORE UPDATE OR DELETE ON worldfuzz.versioned_entities
    FOR EACH ROW EXECUTE FUNCTION worldfuzz.reject_versioned_change();
CREATE TRIGGER immutable_blob_objects BEFORE UPDATE OR DELETE ON worldfuzz.blob_objects
    FOR EACH ROW EXECUTE FUNCTION worldfuzz.reject_versioned_change();
CREATE TRIGGER immutable_split_generations BEFORE UPDATE OR DELETE ON worldfuzz.split_generation_groups
    FOR EACH ROW EXECUTE FUNCTION worldfuzz.reject_versioned_change();
CREATE TRIGGER immutable_dataset_generations BEFORE UPDATE OR DELETE ON worldfuzz.dataset_generation_members
    FOR EACH ROW EXECUTE FUNCTION worldfuzz.reject_versioned_change();

INSERT INTO worldfuzz.corpus_schema_migrations(version, name, applied_at)
    VALUES (2, '002_m1_catalog', EXTRACT(EPOCH FROM clock_timestamp()));
COMMIT;
